# Project conventions

- Work incrementally and implement only the current task.
- Keep the structure simple. Avoid unnecessary frameworks, duplicate
  documentation, generated reports, and speculative features.
- Use English for all UI text, charts, messages, code, comments, docstrings,
  documentation, and commit messages.
- Communicate progress and explain results to the user in Chinese.
- Keep secrets in environment variables; never commit credentials.
- Store timestamps as timezone-aware UTC and display them in `Europe/Stockholm`.
- Explicitly document price units, tax treatment, and time resolution when data
  integration begins.
- Handle daylight-saving time without assuming every local day has 24 hours.
- Use only information available before the forecast cutoff. Preserve forecast
  issue times, input data versions, and predictions to prevent data leakage.
- Clearly distinguish model forecasts, published day-ahead prices, and baseline
  forecasts.
- Evaluate forecasts against simple baselines using chronological validation.
