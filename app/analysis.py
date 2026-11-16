"""Transactions -> dashboard numbers (plain dicts, ready to send as JSON)."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .categorizer import tag
from .parsers.base import ParsedStatement

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def inr(n: float, decimals: int = 0) -> str:
    """Indian digit grouping: 1041697.07 -> ₹10,41,697"""
    neg, n = n < 0, abs(round(n, decimals))
    whole, _, frac = f"{n:.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{'-' if neg else ''}₹{whole}{('.' + frac) if decimals else ''}"


def build_frame(st: ParsedStatement) -> pd.DataFrame:
    rows = []
    for i, t in enumerate(st.transactions):
        is_debit = t.debit > 0 or (t.credit == 0)
        tg = tag(t.description, is_debit)
        rows.append({
            "id": i, "date": pd.Timestamp(t.date), "description": t.description,
            "debit": t.debit, "credit": t.credit, "balance": t.balance,
            "kind": tg.kind, "payee": tg.payee, "category": tg.category,
            "review": t.needs_review, "fixed": t.auto_fixed,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["month"] = df["date"].dt.strftime("%Y-%m")
    df["weekday"] = df["date"].dt.weekday
    return df


def statement_check(df: pd.DataFrame, st: ParsedStatement) -> dict:
    """Compare what we parsed with the totals the bank printed on the last page."""
    out = {"checked": False, "ok": None, "review_rows": int(df["review"].sum()), "fixed_rows": int(df["fixed"].sum()),
           "parsed_debits": round(float(df["debit"].sum()), 2), "parsed_credits": round(float(df["credit"].sum()), 2)}
    if st.stated_total_debits is not None and st.stated_total_credits is not None:
        dd = round(out["parsed_debits"] - st.stated_total_debits, 2)
        dc = round(out["parsed_credits"] - st.stated_total_credits, 2)
        out.update(checked=True, ok=abs(dd) < 0.01 and abs(dc) < 0.01, diff_debits=dd, diff_credits=dc,
                   stated_debits=st.stated_total_debits, stated_credits=st.stated_total_credits)
    return out


def months_list(df: pd.DataFrame) -> list[dict]:
    return [{"id": m, "label": pd.Timestamp(m + "-01").strftime("%b %Y")} for m in sorted(df["month"].unique())]


def analyze(df: pd.DataFrame, month: Optional[str] = None) -> dict:
    all_df = df
    if month:
        df = df[df["month"] == month]
    spend = df[df["debit"] > 0]
    income = df[df["credit"] > 0]

    # month-by-month always covers the whole statement (so it works as a navigator)
    monthly = (all_df.groupby("month").agg(money_in=("credit", "sum"), money_out=("debit", "sum")).reset_index())
    monthly["label"] = monthly["month"].map(lambda m: pd.Timestamp(m + "-01").strftime("%b %y"))

    total_out, total_in = float(spend["debit"].sum()), float(income["credit"].sum())
    days = max(1, (df["date"].max() - df["date"].min()).days + 1) if len(df) else 1

    cats = spend.groupby("category").agg(amount=("debit", "sum"), count=("debit", "size")).sort_values("amount", ascending=False)
    cat_list = [{"name": n, "amount": round(r.amount, 2), "count": int(r["count"]),
                 "pct": round(100 * r.amount / total_out, 1) if total_out else 0} for n, r in cats.iterrows()]
    src = income.groupby("category").agg(amount=("credit", "sum"), count=("credit", "size")).sort_values("amount", ascending=False)

    daily = df.dropna(subset=["balance"]).groupby("date")["balance"].last()
    wk = spend.groupby("weekday")["debit"].agg(["sum", "size"]).reindex(range(7), fill_value=0)

    payees = spend[spend["payee"] != ""].groupby("payee").agg(total=("debit", "sum"), times=("debit", "size")).sort_values("total", ascending=False).head(8)
    top = spend.sort_values("debit", ascending=False).head(10)

    return {
        "totals": {
            "money_in": round(total_in, 2), "money_out": round(total_out, 2), "net": round(total_in - total_out, 2),
            "transactions": int(len(df)), "payments": int(len(spend)), "receipts": int(len(income)),
            "closing_balance": float(df["balance"].dropna().iloc[-1]) if df["balance"].notna().any() else None,
            "avg_daily_spend": round(total_out / days, 2),
            "from": df["date"].min().strftime("%d %b %Y") if len(df) else "", "to": df["date"].max().strftime("%d %b %Y") if len(df) else "",
        },
        "monthly": [{"id": r.month, "label": r.label, "money_in": round(r.money_in, 2), "money_out": round(r.money_out, 2)} for r in monthly.itertuples()],
        "categories": cat_list,
        "income_sources": [{"name": n, "amount": round(r.amount, 2), "count": int(r["count"])} for n, r in src.iterrows()],
        "balance": [{"date": d.strftime("%Y-%m-%d"), "balance": round(float(b), 2)} for d, b in daily.items()],
        "weekday": [{"day": WEEKDAYS[i], "amount": round(float(r["sum"]), 2), "count": int(r["size"])} for i, r in wk.iterrows()],
        "top_expenses": [{"date": r.date.strftime("%d %b %Y"), "to": r.payee or r.category, "amount": r.debit, "category": r.category} for r in top.itertuples()],
        "top_payees": [{"name": n, "total": round(r.total, 2), "times": int(r.times)} for n, r in payees.iterrows()],
        "insights": _insights(df, spend, monthly, total_out, days, bool(month)),
    }


def _insights(df, spend, monthly, total_out, days, single_month) -> list[str]:
    tips: list[str] = []
    if spend.empty:
        return tips
    big = spend.loc[spend["debit"].idxmax()]
    who = f" to {big['payee']}" if big["payee"] else ""
    tips.append(f"Your biggest single payment was {inr(big['debit'])}{who} on {big['date'].strftime('%d %b %Y')}.")
    if not single_month and len(monthly) > 1:
        top_m = monthly.loc[monthly["money_out"].idxmax()]
        tips.append(f"You spent the most in {pd.Timestamp(top_m['month'] + '-01').strftime('%B %Y')}: {inr(top_m['money_out'])}.")
    small = spend[spend["debit"] < 100]
    if len(small) >= 5:
        tips.append(f"{len(small)} of your {len(spend)} payments were under ₹100. They add up to {inr(small['debit'].sum())}.")
    spend_days = spend["date"].nunique()
    tips.append(f"You made payments on {spend_days} of {days} days, about {inr(total_out / days)} per day on average.")
    freq = spend[spend["payee"] != ""].groupby("payee").size().sort_values(ascending=False)
    if len(freq) and freq.iloc[0] >= 5:
        tips.append(f"You paid {freq.index[0]} the most often: {int(freq.iloc[0])} times.")
    return tips
