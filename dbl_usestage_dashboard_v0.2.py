import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import psycopg2
import os
# -----------------------------------------------------------------------------
# 1. CONFIGURATION
# -----------------------------------------------------------------------------

st.set_page_config(
    page_title="Use-Stage Carbon Dashboard",
    layout="wide",
)

# Replace this with your Neon connection string
# Example from Neon:
# postgresql://<user>:<password>@<host>/<dbname>?sslmode=require

DB_URL = st.secrets["DB_URL"]

# Building constants
FLOOR_AREA_M2 = 18_000   # adjust as needed


# -----------------------------------------------------------------------------
# 2. DATABASE HELPERS
# -----------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def get_connection():
    return psycopg2.connect(DB_URL)


@st.cache_data(show_spinner=False)
def load_data():
    conn = get_connection()

    df_yearly = pd.read_sql(
        """
        SELECT
            year,
            energy_consumption,
            energy_emissions,
            water_consumption,
            water_emissions,
            waste_consumption,
            waste_emissions,
            transport_consumption,
            transport_emissions,
            solar_pv_kwh,
            solar_thermal_kwh,
            renewable_offset
        FROM yearly_data
        ORDER BY year DESC;
        """,
        conn,
    )

    df_comfort = pd.read_sql(
        """
        SELECT
            year,
            iaq_score,
            thermal_hours,
            lighting_score,
            acoustic_score
        FROM comfort_data
        ORDER BY year DESC;
        """,
        conn,
    )

    df_cost = pd.read_sql(
        """
        SELECT
            category,
            unit_cost,
            currency
        FROM cost_rates;
        """,
        conn,
    )

    conn.close()
    return df_yearly, df_comfort, df_cost


# -----------------------------------------------------------------------------
# 3. METRIC HELPERS
# -----------------------------------------------------------------------------

def total_gross_emissions(row):
    return (
        row["energy_emissions"]
        + row["water_emissions"]
        + row["waste_emissions"]
        + row["transport_emissions"]
    )


def total_net_emissions(row):
    gross = total_gross_emissions(row)
    offset = row["renewable_offset"]
    return max(0.0, gross - offset)


def intensity_per_area(net_tco2):
    # kgCO2e/m²·yr
    return (net_tco2 * 1000.0) / FLOOR_AREA_M2


def build_historical_df(df_yearly):
    rows = []
    for _, r in df_yearly.sort_values("year").iterrows():
        gross = total_gross_emissions(r)
        net = total_net_emissions(r)
        rows.append(
            {
                "Year": int(r["year"]),
                "Energy (kWh)": int(r["energy_consumption"]),
                "Energy (tCO₂e)": r["energy_emissions"],
                "Water (m³)": int(r["water_consumption"]),
                "Water (tCO₂e)": r["water_emissions"],
                "Waste (kg)": int(r["waste_consumption"]),
                "Waste (tCO₂e)": r["waste_emissions"],
                "Transport (pkm)": int(r["transport_consumption"]),
                "Transport (tCO₂e)": r["transport_emissions"],
                "Renewable offset (tCO₂e)": r["renewable_offset"],
                "Net emissions (tCO₂e)": net,
            }
        )
    return pd.DataFrame(rows)


def build_renewables_df(df_yearly):
    rows = []
    for _, r in df_yearly.sort_values("year").iterrows():
        gross = total_gross_emissions(r)
        offset = r["renewable_offset"]
        total_ren = r["solar_pv_kwh"] + r["solar_thermal_kwh"]
        share = (offset / gross * 100.0) if gross else 0.0
        rows.append(
            {
                "Year": int(r["year"]),
                "Solar PV (kWh)": int(r["solar_pv_kwh"]),
                "Solar thermal (kWh)": int(r["solar_thermal_kwh"]),
                "Total renewables (kWh)": int(total_ren),
                "Offset (tCO₂e)": offset,
                "Offset share (%)": share,
            }
        )
    return pd.DataFrame(rows)


def build_cost_df(df_yearly_row, df_cost_rates):
    rows = []

    # Map DB columns to categories
    cat_map = {
        "energy": ("energy_consumption", "kWh"),
        "water": ("water_consumption", "m³"),
        "waste": ("waste_consumption", "kg"),
        "transport": ("transport_consumption", "pkm"),
    }

    for cat_key, (col_name, unit) in cat_map.items():
        cons = df_yearly_row[col_name]
        rate_row = df_cost_rates[df_cost_rates["category"] == cat_key].iloc[0]
        rate = rate_row["unit_cost"]
        currency = rate_row["currency"]
        annual_cost = cons * rate
        rows.append(
            {
                "Category": cat_key.capitalize(),
                "Consumption": cons,
                "Unit": unit,
                "Unit cost": f"{rate:.3f} {currency}",
                "Annual cost (€)": annual_cost,
            }
        )

    df = pd.DataFrame(rows)
    total = df["Annual cost (€)"].sum()
    if total > 0:
        df["Share of total (%)"] = df["Annual cost (€)"] / total * 100.0
    else:
        df["Share of total (%)"] = 0.0

    return df, total


# -----------------------------------------------------------------------------
# 4. LOAD DATA
# -----------------------------------------------------------------------------

df_yearly, df_comfort, df_cost_rates = load_data()

if df_yearly.empty:
    st.error("No data found in yearly_data table.")
    st.stop()

years = sorted(df_yearly["year"].unique(), reverse=True)
baseline_year = int(df_yearly["year"].min())


# -----------------------------------------------------------------------------
# 5. SIDEBAR
# -----------------------------------------------------------------------------

st.sidebar.title("Use-Stage Carbon Dashboard")

section = st.sidebar.radio(
    "Sections",
    ["Overview", "Energy", "Water", "Waste", "Transport", "Comfort", "Cost"],
)

year = st.sidebar.selectbox(
    "Reporting year",
    years,
    index=0,
)

row_year = df_yearly[df_yearly["year"] == year].iloc[0]
row_baseline = df_yearly[df_yearly["year"] == baseline_year].iloc[0]


# -----------------------------------------------------------------------------
# 6. KPI CALCULATIONS
# -----------------------------------------------------------------------------

net_em = total_net_emissions(row_year)
baseline_net = total_net_emissions(row_baseline)

delta_net = (
    ((baseline_net - net_em) / baseline_net) * 100.0
    if baseline_net
    else 0.0
)

net_intensity = intensity_per_area(net_em)
baseline_intensity = intensity_per_area(baseline_net)
delta_intensity = (
    ((baseline_intensity - net_intensity) / baseline_intensity) * 100.0
    if baseline_intensity
    else 0.0
)

energy_cons = row_year["energy_consumption"]
baseline_energy_cons = row_baseline["energy_consumption"]
delta_energy = (
    ((baseline_energy_cons - energy_cons) / baseline_energy_cons) * 100.0
    if baseline_energy_cons
    else 0.0
)

gross_em = total_gross_emissions(row_year)
offset = row_year["renewable_offset"]
offset_share = (offset / gross_em * 100.0) if gross_em else 0.0


# -----------------------------------------------------------------------------
# 7. PAGE HEADER + KPI CARDS
# -----------------------------------------------------------------------------

st.title("Use-Stage Carbon Dashboard")
st.caption("Operational consumption, emissions, comfort and costs")

st.subheader(f"{section} – {year}")

kpi_cols = st.columns(4)
with kpi_cols[0]:
    st.metric(
        "Net operational carbon",
        f"{net_em:,.0f} tCO₂e/yr",
        f"{delta_net:+.1f}% vs {baseline_year}",
    )
with kpi_cols[1]:
    st.metric(
        "Energy consumption",
        f"{energy_cons:,.0f} kWh/yr",
        f"{delta_energy:+.1f}% vs {baseline_year}",
    )
with kpi_cols[2]:
    st.metric(
        "Carbon intensity (net)",
        f"{net_intensity:,.1f} kgCO₂e/m²·yr",
        f"{delta_intensity:+.1f}% vs {baseline_year}",
    )
with kpi_cols[3]:
    st.metric(
        "Renewables offset",
        f"{offset:,.0f} tCO₂e/yr",
        f"{offset_share:.1f}% of gross emissions",
    )

st.markdown("---")

# Pie chart for current year
pie_df = pd.DataFrame(
    {
        "Category": ["Energy", "Water", "Waste", "Transport"],
        "Emissions": [
            row_year["energy_emissions"],
            row_year["water_emissions"],
            row_year["waste_emissions"],
            row_year["transport_emissions"],
        ],
    }
)
fig_pie = px.pie(
    pie_df,
    names="Category",
    values="Emissions",
    hole=0.5,
    title="Gross emissions by category",
)
st.plotly_chart(fig_pie, use_container_width=True)


# -----------------------------------------------------------------------------
# 8. SECTION-SPECIFIC CONTENT
# -----------------------------------------------------------------------------

if section == "Overview":
    st.markdown("### Historical overview (5 years)")
    hist_df = build_historical_df(df_yearly)
    st.dataframe(hist_df, use_container_width=True)

elif section == "Energy":
    st.markdown("### Energy details")
    energy_df = pd.DataFrame(
        {
            "Metric": ["Consumption", "Emissions", "Renewable offset"],
            "Value": [
                row_year["energy_consumption"],
                row_year["energy_emissions"],
                row_year["renewable_offset"],
            ],
            "Units": ["kWh/yr", "tCO₂e/yr", "tCO₂e/yr"],
        }
    )
    st.table(energy_df)

    st.markdown("### On-site renewables (PV & solar thermal)")
    ren_df = build_renewables_df(df_yearly)
    fig_ren = go.Figure()
    fig_ren.add_bar(
        x=ren_df["Year"], y=ren_df["Solar PV (kWh)"], name="Solar PV (kWh/yr)"
    )
    fig_ren.add_bar(
        x=ren_df["Year"],
        y=ren_df["Solar thermal (kWh)"],
        name="Solar thermal (kWh/yr)",
    )
    fig_ren.add_trace(
        go.Scatter(
            x=ren_df["Year"],
            y=ren_df["Offset (tCO₂e)"],
            name="Carbon offset (tCO₂e/yr)",
            yaxis="y2",
            mode="lines+markers",
        )
    )
    fig_ren.update_layout(
        barmode="stack",
        yaxis=dict(title="Renewable generation (kWh/yr)"),
        yaxis2=dict(
            title="Offset (tCO₂e/yr)",
            overlaying="y",
            side="right",
        ),
        legend=dict(orientation="h"),
    )
    st.plotly_chart(fig_ren, use_container_width=True)

elif section in ["Water", "Waste", "Transport"]:
    key_map = {
        "Water": ("water_consumption", "water_emissions", "m³"),
        "Waste": ("waste_consumption", "waste_emissions", "kg"),
        "Transport": ("transport_consumption", "transport_emissions", "pkm"),
    }
    col_cons, col_em, unit = key_map[section]
    st.markdown(f"### {section} details")
    df_sec = pd.DataFrame(
        {
            "Metric": ["Consumption", "Emissions"],
            "Value": [row_year[col_cons], row_year[col_em]],
            "Units": [unit + "/yr", "tCO₂e/yr"],
        }
    )
    st.table(df_sec)

elif section == "Comfort":
    st.markdown("### Comfort indicators")
    row_comfort = df_comfort[df_comfort["year"] == year].iloc[0]

    c_cols = st.columns(4)
    with c_cols[0]:
        st.metric(
            "Indoor air quality index",
            f"{row_comfort['iaq_score']} /100",
            "Good IAQ" if row_comfort["iaq_score"] >= 85 else "",
        )
    with c_cols[1]:
        st.metric(
            "Time outside thermal comfort",
            f"{row_comfort['thermal_hours']} hours/yr",
            (
                "Within comfort targets"
                if row_comfort["thermal_hours"] <= 150
                else "Above target"
            ),
        )
    with c_cols[2]:
        st.metric(
            "Lighting & visual comfort",
            f"{row_comfort['lighting_score']} /100",
            (
                "Good daylight & glare control"
                if row_comfort["lighting_score"] >= 80
                else ""
            ),
        )
    with c_cols[3]:
        st.metric(
            "Acoustics & noise protection",
            f"{row_comfort['acoustic_score']} /100",
            (
                "Good"
                if row_comfort["acoustic_score"] >= 80
                else "Consider noise mitigation"
            ),
        )

    st.info(
        "Comfort indicators are dimensionless scores on a 0–100 scale, "
        "aggregated from underlying physical measurements (CO₂, PM₂.₅, noise levels, illuminance, etc.)."
    )

elif section == "Cost":
    st.markdown("### Annual operating costs and life cycle cost")
    cost_df, total_annual_cost = build_cost_df(row_year, df_cost_rates)
    st.dataframe(cost_df, use_container_width=True)

    fig_cost = px.bar(
        cost_df,
        x="Category",
        y="Annual cost (€)",
        title="Annual cost by category",
        text_auto=".0f",
    )
    st.plotly_chart(fig_cost, use_container_width=True)

    LIFE_CYCLE_YEARS = 20
    lcc_total = total_annual_cost * LIFE_CYCLE_YEARS
    st.metric(
        "Life cycle cost (20 years, no discounting)",
        f"{lcc_total:,.0f} €",
        help="Simple multiplication of current annual cost by 20 years.",
    )

# -----------------------------------------------------------------------------
# 9. DATA SOURCES TABLE (STATIC, CAN BE MOVED TO DB LATER)
# -----------------------------------------------------------------------------

st.markdown("### Data sources (meters, surveys, sensors)")

data_sources = [
    {
        "Name": "Grid electricity main meter",
        "Category": "Energy",
        "Unit": "kWh",
        "Coverage": "Whole building",
    },
    {
        "Name": "Natural gas boiler",
        "Category": "Energy",
        "Unit": "m³",
        "Coverage": "Heating only",
    },
    {
        "Name": "Potable water main",
        "Category": "Water",
        "Unit": "m³",
        "Coverage": "Whole building",
    },
    {
        "Name": "Mixed non-hazardous waste",
        "Category": "Waste",
        "Unit": "kg",
        "Coverage": "Building",
    },
    {
        "Name": "Staff commuting survey",
        "Category": "Transport",
        "Unit": "pkm",
        "Coverage": "72% staff",
    },
    {
        "Name": "Comfort monitoring (IAQ & thermal)",
        "Category": "Comfort",
        "Unit": "sensors",
        "Coverage": "Offices & hangar",
    },
]

src_df = pd.DataFrame(data_sources)
if section != "Overview":
    src_df = src_df[src_df["Category"].str.lower() == section.lower()]
st.dataframe(src_df, use_container_width=True)
