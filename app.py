"""Streamlit landing page for Electricity Radar."""

import streamlit as st

st.set_page_config(page_title="Electricity Radar", page_icon="⚡")

st.title("Electricity Radar")
st.write(
    "Electricity Radar aims to forecast Swedish electricity prices across SE1–SE4. "
    "The initial focus is forecasting next-day SE3 day-ahead prices before publication, "
    "saving predictions, and evaluating them against published prices and simple baselines."
)
st.write(
    "Weather, wind generation, electricity demand, and machine learning "
    "will be added incrementally."
)
st.info("Data sources are not connected yet.")
