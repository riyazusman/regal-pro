# RegalPro

Showtimes explorer, cluster theater discovery, and multi-movie itinerary planner for Regal Cinemas.

**Live Application:** [regalpro.app](https://regalpro.app)

---

## Overview

This repository contains the source code for the RegalPro web application and its itinerary scheduling engine. 

> **Note on Local Execution:** This repository is maintained primarily for code inspection, architectural reference, and engine evaluation. Full end-to-end operation relies on scheduled ingestion pipelines, admin data synchronizers, and managed Supabase backend services that run on private production infrastructure.

---

## Tech Stack & Architecture

- **UI & Presentation**: Streamlit (Responsive layouts, session state synchronization, deep-link routing)
- **Scheduling Engine**: Pure Python constraint satisfaction engine (`core/engine.py`)
- **Data & Telemetry**: Supabase (PostgreSQL) and Regal API integration via `curl_cffi`
- **Geolocation & Mapping**: `pgeocode` and `geopy` (Nominatim reverse geocoding)
- **Client Preferences**: URL-safe base64 browser cookie serialization (`regal_pro_prefs`)

---

## Project Structure

```
RegalPro/
├── regal_pro.py          # Main Streamlit application entrypoint
├── redirect_app.py       # Lightweight redirection service for legacy domains
├── core/
│   ├── engine.py         # Multi-movie itinerary algorithms & scoring
│   ├── models.py         # Data models and SchedulerConstraints dataclasses
│   ├── preferences.py    # Client cookie management, schema validation & bounds
│   ├── share_utils.py    # Deep-link token serialization / deserialization
│   ├── date_utils.py     # Calendar date parsing and chronological sorting
│   └── text_utils.py     # Slugification and Regal URL builders
├── tests/
│   ├── test_preferences.py
│   ├── test_scheduler_engine.py
│   ├── test_share_utils.py
│   └── test_text_utils.py
└── requirements.txt      # Python dependencies
```

---

## Testing

The core algorithms, preference serialization, date utilities, and token decoders are verified via standard Python unit tests:

```bash
python -m unittest discover tests
```

---

## Issues & Support

- Bug reports & feedback: [GitHub Issues](https://github.com/riyazusman/regal-pro/issues)
- Support the project: [Buy Me a Coffee](https://buymeacoffee.com/riyazusman)