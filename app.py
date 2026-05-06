import pandas as pd
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


LHV = {"Fuel Oil": 44533, "Fuel Gas": 52837, "Natural Gas": 47158}  # kJ/kg
EMISSIONS = {"Fuel Oil": 70.4, "Fuel Gas": 51.5, "Natural Gas": 56.5}  # kg CO2/GJ
FUEL_COLORS = {"Fuel Oil": "#e74c3c", "Fuel Gas": "#f1c40f", "Natural Gas": "#2ecc71"}
POWER_COLOR = "#3498db"
CARBON_TAX = 50.0  # $/tonne CO2
ELECTRICITY_USD_PER_MWH = 85.0


st.set_page_config(page_title="Refinery Energy Manager", layout="wide")

st.markdown(
    """
    <style>
    .stApp {
        background: #c0c0c0;
    }
    [data-testid="stSidebar"] {
        background: #ffffff;
    }
    [data-testid="stSidebar"] * {
        color: #111827;
    }
    [data-testid="stSidebar"] input {
        color: #111827;
        background: #f9fafb;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def fuel_energy_gj_h(flow_kg_h, fuel):
    return flow_kg_h * LHV[fuel] / 1_000_000


def fuel_power_kw(flow_kg_h, fuel):
    return fuel_energy_gj_h(flow_kg_h, fuel) * 277.777778


def fuel_cost_usd_h(flow_kg_h, fuel, prices):
    return flow_kg_h / 1000 * prices[fuel]


def fuel_usd_gj(fuel, prices):
    return prices[fuel] / (LHV[fuel] / 1000)


def fuel_effective_usd_gj(fuel, prices):
    return fuel_usd_gj(fuel, prices) + (EMISSIONS[fuel] / 1000 * CARBON_TAX)


def carbon_tax_usd_h(flow_kg_h, fuel):
    co2_kg_h = fuel_energy_gj_h(flow_kg_h, fuel) * EMISSIONS[fuel]
    return co2_kg_h / 1000 * CARBON_TAX


def electric_cost_usd_h(power_kw):
    return power_kw / 1000 * ELECTRICITY_USD_PER_MWH


def electric_usd_kw():
    return ELECTRICITY_USD_PER_MWH / 1000


def make_stream_rows(unit_inputs, prices):
    rows = []
    for unit, values in unit_inputs.items():
        for fuel in LHV:
            if fuel not in values:
                continue
            flow = values[fuel]
            energy = fuel_energy_gj_h(flow, fuel)
            rows.append(
                {
                    "Unit": unit,
                    "Stream": fuel,
                    "Flow kg/h": flow,
                    "Power kW": fuel_power_kw(flow, fuel),
                    "Energy GJ/h": energy,
                    "USD/GJ": fuel_usd_gj(fuel, prices),
                    "USD/kW": None,
                    "USD/h": fuel_cost_usd_h(flow, fuel, prices),
                    "CO2 kg/h": energy * EMISSIONS[fuel],
                    "Carbon tax USD/h": carbon_tax_usd_h(flow, fuel),
                }
            )
        electric_kw = values["Electricity kW"]
        rows.append(
            {
                "Unit": unit,
                "Stream": "Electricity",
                "Flow kg/h": 0,
                "Power kW": electric_kw,
                "Energy GJ/h": electric_kw / 277.777778,
                "USD/GJ": None,
                "USD/kW": electric_usd_kw(),
                "USD/h": electric_cost_usd_h(electric_kw),
                "CO2 kg/h": 0,
                "Carbon tax USD/h": 0,
            }
        )
    return pd.DataFrame(rows)


def unit_summary(streams):
    return (
        streams.groupby("Unit", as_index=False)
        .agg(
            {
                "Power kW": "sum",
                "Energy GJ/h": "sum",
                "USD/h": "sum",
                "Carbon tax USD/h": "sum",
            }
        )
        .assign(**{"Total USD/h": lambda df: df["USD/h"] + df["Carbon tax USD/h"]})
    )


def optimize_fuel_blend(streams, prices):
    fuel_order = sorted(LHV, key=lambda fuel: fuel_effective_usd_gj(fuel, prices))
    fuel_gas_cap_kg_h = streams.loc[streams["Stream"] == "Fuel Gas", "Flow kg/h"].sum()
    remaining_fuel_gas_cap = fuel_gas_cap_kg_h
    rows = []

    for unit in streams["Unit"].unique():
        unit_rows = streams[streams["Unit"] == unit]
        fuel_rows = unit_rows[unit_rows["Stream"].isin(LHV.keys())]
        electric_cost = unit_rows.loc[unit_rows["Stream"] == "Electricity", "USD/h"].sum()
        fuel_energy = fuel_rows["Energy GJ/h"].sum()
        actual_fuel_cost = fuel_rows["USD/h"].sum() + fuel_rows["Carbon tax USD/h"].sum()
        actual_total_cost = actual_fuel_cost + electric_cost
        remaining_energy = fuel_energy
        target_flows = {f"Target {fuel} kg/h": 0 for fuel in LHV}
        target_fuel_cost = 0
        target_fuel_names = []

        for fuel in fuel_order:
            if remaining_energy <= 0:
                break

            if fuel == "Fuel Gas":
                available_energy = fuel_energy_gj_h(remaining_fuel_gas_cap, "Fuel Gas")
                allocated_energy = min(remaining_energy, available_energy)
                allocated_flow = allocated_energy * 1_000_000 / LHV["Fuel Gas"] if allocated_energy else 0
                remaining_fuel_gas_cap -= allocated_flow
            else:
                allocated_energy = remaining_energy
                allocated_flow = allocated_energy * 1_000_000 / LHV[fuel] if allocated_energy else 0

            if allocated_energy <= 0:
                continue

            target_flows[f"Target {fuel} kg/h"] = allocated_flow
            target_fuel_cost += allocated_energy * fuel_effective_usd_gj(fuel, prices)
            target_fuel_names.append(fuel)
            remaining_energy -= allocated_energy

        target_total_cost = target_fuel_cost + electric_cost
        weighted_rate = target_fuel_cost / fuel_energy if fuel_energy else 0
        rows.append(
            {
                "Unit": unit,
                "Same energy GJ/h": fuel_energy,
                "Optimum blend": " + ".join(target_fuel_names) if target_fuel_names else "No fuel",
                "Effective USD/GJ": weighted_rate,
                "Actual total USD/h": actual_total_cost,
                "Target total USD/h": target_total_cost,
                "Savings USD/h": actual_total_cost - target_total_cost,
                "Actual / Target %": (actual_total_cost / target_total_cost * 100) if target_total_cost else 0,
                **target_flows,
            }
        )

    return pd.DataFrame(rows), fuel_gas_cap_kg_h


def format_money(value):
    return f"${value:,.2f}"


def format_money_1(value):
    return f"${value:,.1f}"


def add_line(fig, points, color, name=None, width=4, dash=None):
    x, y = zip(*points)
    fig.add_trace(
        go.Scatter(
            x=x,
            y=y,
            mode="lines",
            line=dict(color=color, width=width, shape="hv", dash=dash),
            hoverinfo="skip",
            showlegend=name is not None,
            name=name,
        )
    )


def add_node(fig, x, y, color):
    fig.add_trace(
        go.Scatter(
            x=[x],
            y=[y],
            mode="markers",
            marker=dict(color=color, size=8, line=dict(color="#f8fafc", width=1)),
            hoverinfo="skip",
            showlegend=False,
        )
    )


def add_arrow(fig, x, y, color, ax=None, ay=None, angle=0):
    ax = x - 0.035 if ax is None else ax
    ay = y if ay is None else ay
    fig.add_annotation(
        x=x,
        y=y,
        ax=ax,
        ay=ay,
        xref="x",
        yref="y",
        axref="x",
        ayref="y",
        showarrow=True,
        arrowhead=3,
        arrowsize=1.2,
        arrowwidth=2,
        arrowcolor=color,
        text="",
        textangle=angle,
    )


def unit_label(unit, unit_rows):
    fuels = unit_rows[unit_rows["Stream"].isin(LHV.keys())]
    total_power = unit_rows["Power kW"].sum()
    total_energy_cost = unit_rows["USD/h"].sum()
    total_tax = unit_rows["Carbon tax USD/h"].sum()
    fuel_lines = []
    for _, row in fuels.iterrows():
        if row["Flow kg/h"] > 0:
            fuel_lines.append(
                f"{row['Stream']}: {row['Power kW']:,.0f} kW | {format_money(row['USD/h'])}/h"
            )
    if not fuel_lines:
        fuel_lines = ["No fuel"]
    if unit == "MDP":
        return (
            f"<b>Total:</b> {total_power:,.0f} kW"
            + f"<br><b>Energy:</b> {format_money(total_energy_cost)}/h"
            + f"<br><b>CO2 tax:</b> {format_money(total_tax)}/h"
        )
    return (
        "<br>".join(fuel_lines)
        + f"<br><br><b>Total:</b> {total_power:,.0f} kW"
        + f"<br><b>Energy:</b> {format_money(total_energy_cost)}/h"
        + f"<br><b>CO2 tax:</b> {format_money(total_tax)}/h"
    )


def stream_label(row):
    if row["Stream"] == "Electricity":
        return f"Elec: {row['Power kW']:,.0f} kW<br>${electric_usd_kw():,.3f}/kW | {format_money(row['USD/h'])}/h"
    return (
        f"{row['Flow kg/h']:,.0f} kg/h<br>{row['Power kW']:,.0f} kW"
        f"<br>{format_money(row['USD/GJ'])}/GJ | {format_money(row['USD/h'])}/h"
    )


def unit_co2_label(streams, unit):
    co2_kg_h = streams.loc[streams["Unit"] == unit, "CO2 kg/h"].sum()
    return f"<b>CO2</b><br>{co2_kg_h:,.0f} kg/h"


def generate_pfd(streams):
    fig = go.Figure()

    units = {
        "Utilities": {"box": [0.31, 0.50, 0.55, 0.78], "title": (0.43, 0.80), "label": (0.43, 0.63)},
        "Topping": {"box": [0.76, 0.52, 0.96, 0.78], "title": (0.86, 0.80), "label": (0.88, 0.64)},
        "Platforming": {"box": [0.55, 0.16, 0.76, 0.42], "title": (0.655, 0.455), "label": (0.655, 0.28)},
        "MDP": {"box": [0.18, 0.10, 0.39, 0.34], "title": (0.205, 0.31), "label": (0.285, 0.205)},
    }

    for unit, data in units.items():
        x0, y0, x1, y1 = data["box"]
        unit_rows = streams[streams["Unit"] == unit]
        fig.add_shape(
            type="rect",
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
            line=dict(color="#f8fafc", width=2),
            fillcolor="#223447",
        )
        fig.add_annotation(
            x=data["title"][0],
            y=data["title"][1],
            text=f"<b>{unit}</b>",
            showarrow=False,
            xanchor="left" if unit == "MDP" else "center",
            font=dict(color="#f8fafc", size=15),
        )
        fig.add_annotation(
            x=data["label"][0],
            y=data["label"][1],
            text=unit_label(unit, unit_rows),
            showarrow=False,
            align="left",
            font=dict(color="#dcfce7", size=10),
            bgcolor="rgba(5, 10, 20, 0.82)",
            bordercolor="#475569",
            borderpad=5,
        )

    headers = {
        "Fuel Oil": {"y": 0.93, "x0": 0.04, "x1": 0.98},
        "Fuel Gas": {"y": 0.88, "x0": 0.04, "x1": 0.98},
        "Natural Gas": {"y": 0.83, "x0": 0.04, "x1": 0.98},
    }
    for fuel, data in headers.items():
        add_line(fig, [(data["x0"], data["y"]), (data["x1"], data["y"])], FUEL_COLORS[fuel], fuel)
        fig.add_annotation(
            x=data["x0"],
            y=data["y"] + 0.018,
            text=f"<b>{fuel} header</b>",
            showarrow=False,
            xanchor="left",
            font=dict(color=FUEL_COLORS[fuel], size=12),
        )

    connections = {
        "Utilities": {"x": 0.31, "ys": {"Fuel Oil": 0.70, "Fuel Gas": 0.66, "Natural Gas": 0.62}},
        "Topping": {"x": 0.76, "ys": {"Fuel Oil": 0.735, "Fuel Gas": 0.675, "Natural Gas": 0.60}},
        "Platforming": {"x": 0.55, "ys": {"Fuel Oil": 0.34, "Fuel Gas": 0.29, "Natural Gas": 0.24}},
    }

    label_offsets = {
        "Utilities": 0.20,
        "Topping": -0.08,
        "Platforming": -0.08,
        "MDP": 0.10,
    }
    stream_label_positions = {
        ("Utilities", "Fuel Oil"): (0.285, 0.725),
        ("Utilities", "Fuel Gas"): (0.285, 0.625),
        ("Utilities", "Natural Gas"): (0.285, 0.565),
        ("Topping", "Fuel Oil"): (0.705, 0.675),
        ("Topping", "Fuel Gas"): (0.705, 0.585),
        ("Topping", "Natural Gas"): (0.705, 0.535),
        ("Platforming", "Fuel Gas"): (0.49, 0.245),
        ("Platforming", "Fuel Oil"): (0.49, 0.345),
        ("Platforming", "Natural Gas"): (0.49, 0.205),
    }
    stream_label_anchors = {
        ("Utilities", "Fuel Oil"): "right",
        ("Utilities", "Fuel Gas"): "right",
        ("Utilities", "Natural Gas"): "right",
        ("Topping", "Fuel Oil"): "right",
        ("Topping", "Fuel Gas"): "right",
        ("Topping", "Natural Gas"): "right",
    }

    for unit, data in connections.items():
        for fuel, y_to in data["ys"].items():
            row = streams[(streams["Unit"] == unit) & (streams["Stream"] == fuel)].iloc[0]
            if row["Flow kg/h"] <= 0:
                continue
            header_y = headers[fuel]["y"]
            if unit == "Topping":
                branch_x = {"Fuel Oil": 0.715, "Fuel Gas": 0.745, "Natural Gas": 0.725}[fuel]
            elif unit == "Utilities":
                branch_x = {"Fuel Oil": 0.270, "Fuel Gas": 0.292, "Natural Gas": 0.282}[fuel]
            elif unit == "Platforming":
                branch_x = data["x"] - 0.035
            else:
                branch_x = data["x"]
            line_points = [(branch_x, header_y), (branch_x, y_to), (data["x"], y_to)]
            add_line(
                fig,
                line_points,
                FUEL_COLORS[fuel],
                width=4 if unit in {"Utilities", "Topping"} and fuel in {"Fuel Oil", "Fuel Gas"} else 3,
            )
            if unit in {"Utilities", "Topping"} and fuel in {"Fuel Oil", "Fuel Gas"}:
                add_node(fig, branch_x, header_y, FUEL_COLORS[fuel])
                add_node(fig, branch_x, y_to, FUEL_COLORS[fuel])
            add_arrow(fig, data["x"], y_to, FUEL_COLORS[fuel])
            label_x, label_y = stream_label_positions.get(
                (unit, fuel),
                (min(max(data["x"] + label_offsets[unit], 0.08), 0.94), y_to),
            )
            fig.add_annotation(
                x=label_x,
                y=label_y,
                text=stream_label(row),
                showarrow=False,
                align="left",
                xanchor=stream_label_anchors.get((unit, fuel), "center"),
                font=dict(color="#f8fafc", size=9),
                bgcolor="rgba(15, 23, 42, 0.88)",
                bordercolor=FUEL_COLORS[fuel],
                borderpad=3,
            )

    # Power/electricity lines based on the sketch: Utilities feeds Topping, Platforming, and MDP.
    add_line(fig, [(0.31, 0.50), (0.70, 0.50)], POWER_COLOR, width=3, dash="dot")
    power_routes = {
        "Utilities": [(0.55, 0.56), (0.59, 0.56), (0.59, 0.72), (0.55, 0.72)],
        "Topping": [(0.70, 0.50), (0.70, 0.57), (0.76, 0.57)],
        "Platforming": [(0.61, 0.50), (0.61, 0.42)],
        "MDP": [(0.31, 0.50), (0.25, 0.50), (0.25, 0.34)],
    }
    power_label_positions = {
        "Utilities": (0.615, 0.64),
        "Topping": (0.735, 0.49),
        "Platforming": (0.585, 0.455),
        "MDP": (0.18, 0.39),
    }
    power_label_anchors = {
        "Platforming": "right",
    }
    power_arrow_points = {
        "Utilities": {"ax": 0.585, "ay": 0.72},
        "Topping": {"ax": 0.725, "ay": 0.57},
        "Platforming": {"ax": 0.61, "ay": 0.455},
        "MDP": {"ax": 0.25, "ay": 0.375},
    }
    for unit, route in power_routes.items():
        row = streams[(streams["Unit"] == unit) & (streams["Stream"] == "Electricity")].iloc[0]
        if row["Power kW"] <= 0:
            continue
        add_line(fig, route, POWER_COLOR, width=3, dash="dot")
        arrow = power_arrow_points[unit]
        add_arrow(fig, route[-1][0], route[-1][1], POWER_COLOR, ax=arrow["ax"], ay=arrow["ay"])
        label_x, label_y = power_label_positions[unit]
        fig.add_annotation(
            x=label_x,
            y=label_y,
            text=stream_label(row),
            showarrow=False,
            align="left",
            xanchor=power_label_anchors.get(unit, "center"),
            font=dict(color="#dbeafe", size=9),
            bgcolor="rgba(15, 23, 42, 0.88)",
            bordercolor=POWER_COLOR,
            borderpad=3,
        )

    scope_points = {
        "Utilities": {"scope": (0.50, 0.405), "co2": (0.46, 0.43)},
        "Topping": {"scope": (0.98, 0.49), "co2": (0.94, 0.46)},
        "Platforming": {"scope": (0.74, 0.10), "co2": (0.70, 0.075)},
    }
    for unit, points in scope_points.items():
        x, y = points["scope"]
        fig.add_annotation(
            x=x,
            y=y,
            text="<b>Scope 1</b>",
            showarrow=True,
            arrowhead=2,
            arrowcolor="#94a3b8",
            font=dict(color="#cbd5e1", size=11),
        )
        fig.add_annotation(
            x=points["co2"][0],
            y=points["co2"][1],
            text=unit_co2_label(streams, unit),
            showarrow=False,
            align="center",
            font=dict(color="#f8fafc", size=9),
            bgcolor="rgba(15, 23, 42, 0.88)",
            bordercolor="#94a3b8",
            borderpad=4,
        )

    fig.update_layout(
        template="plotly_dark",
        plot_bgcolor="#c0c0c0",
        paper_bgcolor="#c0c0c0",
        xaxis=dict(visible=False, range=[0, 1]),
        yaxis=dict(visible=False, range=[0, 1]),
        margin=dict(l=10, r=10, t=10, b=10),
        height=720,
        showlegend=True,
        legend=dict(x=0.82, y=0.99, bgcolor="rgba(15, 23, 42, 0.75)"),
    )
    return fig


st.title("Refinery Energy Manager")

with st.sidebar:
    st.header("Fuel Prices")
    prices = {
        "Fuel Oil": st.number_input("Fuel Oil ($/tonne)", value=578.0, min_value=0.0, step=1.0),
        "Fuel Gas": st.number_input("Fuel Gas ($/tonne)", value=271.0, min_value=0.0, step=1.0),
        "Natural Gas": st.number_input("Natural Gas ($/tonne)", value=300.0, min_value=0.0, step=1.0),
    }
    st.number_input(
        "Carbon tax ($/tonne CO2)",
        value=CARBON_TAX,
        min_value=0.0,
        step=1.0,
        disabled=True,
    )

    st.header("Unit Fuel Flow Rates")
    st.caption("kg/h for fuels, kW for electricity")
    defaults = {
        "Utilities": {"Fuel Oil": 688, "Fuel Gas": 1424, "Natural Gas": 0, "Electricity kW": 670},
        "Topping": {"Fuel Oil": 319, "Fuel Gas": 2172, "Natural Gas": 0, "Electricity kW": 835},
        "Platforming": {"Fuel Oil": 0, "Fuel Gas": 1340, "Natural Gas": 0, "Electricity kW": 482},
        "MDP": {"Electricity kW": 264},
    }

    unit_inputs = {}
    for unit, values in defaults.items():
        with st.expander(unit, expanded=unit in {"Utilities", "Platforming"}):
            unit_inputs[unit] = {}
            for stream, value in values.items():
                unit_inputs[unit][stream] = st.number_input(
                    f"{unit} {stream}",
                    value=value,
                    min_value=0,
                    step=1,
                    key=f"{unit}-{stream}",
                )

streams = make_stream_rows(unit_inputs, prices)
summary = unit_summary(streams)
optimization, fuel_gas_cap_kg_h = optimize_fuel_blend(streams, prices)

total_energy_cost = streams["USD/h"].sum()
total_carbon_tax = streams["Carbon tax USD/h"].sum()
total_power = streams["Power kW"].sum()
actual_optimized_basis = optimization["Actual total USD/h"].sum()
target_optimized_basis = optimization["Target total USD/h"].sum()
actual_target_pct = (
    actual_optimized_basis / target_optimized_basis * 100 if target_optimized_basis else 0
)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total Power", f"{total_power:,.0f} kW")
k2.metric("Energy Cost", f"{format_money_1(total_energy_cost)}/h")
k3.metric("Carbon Tax", f"{format_money_1(total_carbon_tax)}/h")
k4.metric("Total Cost", f"{format_money_1(total_energy_cost + total_carbon_tax)}/h")
k5.metric("Target Cost", f"{format_money_1(target_optimized_basis)}/h")

st.plotly_chart(generate_pfd(streams), use_container_width=True)

st.subheader("Same-Energy Fuel Blend Optimization")
st.caption(
    "Target keeps each unit at the same fuel energy demand in GJ/h and minimizes fuel plus carbon cost. "
    "Electricity demand is kept unchanged, and optimized Fuel Gas use cannot exceed the operator total."
)
o1, o2, o3, o4 = st.columns(4)
o1.metric("Actual Cost", f"{format_money_1(actual_optimized_basis)}/h")
o2.metric("Target Cost", f"{format_money_1(target_optimized_basis)}/h")
o3.metric("Potential Savings", f"{format_money_1(actual_optimized_basis - target_optimized_basis)}/h")
o4.metric("Actual / Target", f"{actual_target_pct:,.1f}%")
st.info(f"Operator Fuel Gas maximum used by optimization: {fuel_gas_cap_kg_h:,.0f} kg/h")

basis, target = st.columns([0.9, 1.5])
with basis:
    st.markdown("**Fuel Cost Basis**")
    fuel_basis = pd.DataFrame(
        [
            {
                "Fuel": fuel,
                "USD/GJ fuel": fuel_usd_gj(fuel, prices),
                "USD/GJ carbon": EMISSIONS[fuel] / 1000 * CARBON_TAX,
                "Effective USD/GJ": fuel_effective_usd_gj(fuel, prices),
            }
            for fuel in LHV
        ]
    )
    st.dataframe(
        fuel_basis.style.format(
            {
                "USD/GJ fuel": "${:,.2f}",
                "USD/GJ carbon": "${:,.2f}",
                "Effective USD/GJ": "${:,.2f}",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

with target:
    st.markdown("**Target Blend by Unit**")
    st.dataframe(
        optimization.style.format(
            {
                "Same energy GJ/h": "{:,.2f}",
                "Effective USD/GJ": "${:,.2f}",
                "Actual total USD/h": "${:,.2f}",
                "Target total USD/h": "${:,.2f}",
                "Savings USD/h": "${:,.2f}",
                "Actual / Target %": "{:,.1f}%",
                "Target Fuel Oil kg/h": "{:,.0f}",
                "Target Fuel Gas kg/h": "{:,.0f}",
                "Target Natural Gas kg/h": "{:,.0f}",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

left, right = st.columns([1.35, 1])
with left:
    st.subheader("Stream Energy and Cost")
    st.dataframe(
        streams[
            [
                "Unit",
                "Stream",
                "Flow kg/h",
                "Power kW",
                "Energy GJ/h",
                "USD/GJ",
                "USD/kW",
                "USD/h",
                "Carbon tax USD/h",
            ]
        ].style.format(
            {
                "Flow kg/h": "{:,.0f}",
                "Power kW": "{:,.0f}",
                "Energy GJ/h": "{:,.2f}",
                "USD/GJ": "${:,.2f}",
                "USD/kW": "${:,.3f}",
                "USD/h": "${:,.2f}",
                "Carbon tax USD/h": "${:,.2f}",
            },
            na_rep="-",
        ),
        use_container_width=True,
        hide_index=True,
    )

with right:
    st.subheader("Unit Totals")
    st.dataframe(
        summary.style.format(
            {
                "Power kW": "{:,.0f}",
                "Energy GJ/h": "{:,.2f}",
                "USD/h": "${:,.2f}",
                "Carbon tax USD/h": "${:,.2f}",
                "Total USD/h": "${:,.2f}",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )
