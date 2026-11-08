# Electricity Radar

Electricity Radar is a Swedish electricity price forecasting app. The long-term
goal is to collect data for SE1–SE4. The initial focus is to forecast next-day SE3
day-ahead prices before publication, save predictions, and compare them with
published prices and simple baselines. Weather, wind generation, demand, and
machine learning will be added incrementally.

## Current status

The app currently provides a minimal Streamlit landing page. Data sources are
not connected. There is no database, model training, or forecasting pipeline yet.

## Local setup

Python 3.12 is used for development. From the repository root, create a local
virtual environment and install the dependency:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Launch the app from the repository root:

```sh
.venv/bin/python -m streamlit run app.py
```

Open the local URL printed by Streamlit. Stop the server with `Ctrl+C`.
Shell activation is not required.
