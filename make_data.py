"""Generate two fake sales datasets, a year apart, so we have something to analyze.

sales.csv       2024, the baseline year
sales_2025.csv  the year after, with deliberate changes to compare against
"""

import random

import pandas as pd

REGIONS = ["North", "South", "East", "West"]


def generate(filename, year, products, shares, starting_revenue, monthly_growth, seed):
    """Build one year of fake sales and save it as a CSV.

    products:         which products were sold that year
    shares:           what slice of revenue each product takes
    starting_revenue: what each region sells in January, before growth
    monthly_growth:   how much each region grows per month, as a decimal
    seed:             fixes the random numbers so the file is the same every run
    """
    random.seed(seed)

    # "2024-01" through "2024-12". The :02d pads single digits with a zero.
    months = [f"{year}-{month:02d}" for month in range(1, 13)]

    rows = []

    # One row for every combination of region, product, and month.
    for region in REGIONS:
        for product in products:
            for months_elapsed, month in enumerate(months):
                # Compound growth: start * (1 + rate) raised to the months elapsed.
                base = starting_revenue[region] * (1 + monthly_growth[region]) ** months_elapsed

                # A random wobble of plus or minus 10%, so it does not look synthetic.
                wobble = random.uniform(0.9, 1.1)

                revenue = round(base * shares[product] * wobble, 2)

                rows.append(
                    {"region": region, "product": product, "month": month, "revenue": revenue}
                )

    df = pd.DataFrame(rows)
    df.to_csv(filename, index=False)
    print(f"Wrote {filename} with {len(df)} rows")
    return df


# --- 2024: the baseline year -------------------------------------------------
# South booms, West shrinks, North is flat, East grows steadily.
sales_2024 = generate(
    filename="sales.csv",
    year=2024,
    products=["Widget", "Gadget", "Doohickey"],
    shares={"Widget": 0.5, "Gadget": 0.3, "Doohickey": 0.2},
    starting_revenue={"North": 50000, "South": 20000, "East": 35000, "West": 60000},
    monthly_growth={"North": 0.01, "South": 0.08, "East": 0.03, "West": -0.02},
    seed=42,
)

# --- 2025: the year after ----------------------------------------------------
# Deliberate changes to compare against 2024:
#   - Doohickey was discontinued, and a new product called Gizmo replaced it
#   - West reversed: it was the worst performer, now it is the best
#   - East reversed the other way: it was growing, now it is shrinking
#   - South is still growing, but far slower than its 2024 boom
# Each region starts roughly where it ended in December 2024.
sales_2025 = generate(
    filename="sales_2025.csv",
    year=2025,
    products=["Widget", "Gadget", "Gizmo"],
    shares={"Widget": 0.45, "Gadget": 0.30, "Gizmo": 0.25},
    starting_revenue={"North": 55000, "South": 46000, "East": 50000, "West": 46000},
    monthly_growth={"North": 0.02, "South": 0.01, "East": -0.03, "West": 0.05},
    seed=99,
)

print()
print(sales_2025.head())
