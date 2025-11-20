# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Pitmaster is a Python application that combines hardware (RTL-SDR dongle) with Claude AI to provide real-time BBQ smoking assistance. It reads temperature data from wireless BBQ thermometers and provides intelligent cooking advice, ETA predictions, and SMS alerts.

## Essential Commands

### Development and Testing
```bash
# Run all tests
python3 run_tests.py

# Run tests directly with pytest
python3 -m pytest tests -v

# Run specific test file
python3 -m pytest tests/test_ai_pitmaster.py -v

# Run the main application
python3 ai_pitmaster.py
```

### Dependencies
```bash
# Install Python dependencies
pip install -r requirements.txt
```

Dependencies: `anthropic`, `requests`, `scipy` (optional), `pytest`

## Architecture Overview

The application is built around a single main class `ClaudeBBQConversation` in `ai_pitmaster.py` (56:403) that orchestrates all functionality.

### Core Components

**Temperature Reading** (ai_pitmaster.py:355-372)
- Background thread `temp_reader_thread()` runs `rtl_433` subprocess to capture wireless thermometer data
- Parses JSON output from Thermopro TP12 (or compatible 433MHz devices)
- Queues temperature data for main loop processing
- Also captures ambient temperature from nearby weather stations

**Claude Integration** (ai_pitmaster.py:136-151)
- `_ask_claude()` method maintains conversation context with Anthropic API
- Uses Claude Sonnet 4.5 model with temperature=0.2 for consistent advice
- Sends full conversation history (200K token context window)
- Initial context includes `PITMASTER_WISDOM` knowledge base with BBQ fundamentals

**SMS Alerting** (ai_pitmaster.py:115-134)
- `send_sms()` method uses TextBelt API for critical notifications
- Per-alert-type cooldown tracking via `last_sms_time` dict to prevent spam
- Default cooldown: 900 seconds (configurable via `BBQ_SMS_COOLDOWN`)
- Alert types: `pit_crash`, `pit_spike`, `stall`, `done_soon`, `done`

**Mathematical Modeling** (ai_pitmaster.py:282-330)
- `_update_model_estimate()` fits 5-parameter logistic curve to Stage I temperature data
- `_logistic5()` implements the 5PL model: D + (K - D) / ((1 + exp(-k(t - λ)))^γ)
- Predicts wrap time (150°F) and finish time (target temp)
- Requires SciPy `curve_fit` - gracefully degrades if unavailable
- Only fits on last hour of data below 150°F to capture pre-stall behavior

**Stall Detection** (ai_pitmaster.py:253-276)
- `detect_stall_mathematical()` implements Henderson's criterion
- Uses centered 3-point finite difference to calculate α (relative rate)
- Stall detected when: 150°F ≤ meat temp ≤ 170°F AND |α| ≤ 0.03 h⁻¹
- Based on paper: http://www.tlhiv.org/papers/1-33-T-SouthernBarbeque-TeacherVersion.pdf

**Context-Aware Alerting** (ai_pitmaster.py:191-249)
- `check_gradual_trends()` monitors for sustained pit temperature declines
- `_should_alert_about_temp_decline()` uses conversation context to avoid redundant alerts
- Tracks recent user actions in `recent_user_actions` deque to detect fuel-related mentions
- Temperature recovery tracking prevents alerts while temp is stabilizing after fuel addition

**Session Persistence** (ai_pitmaster.py:117-251)
- `save_session()` serializes all cook state to JSON (conversation, temps, alerts, model state)
- `load_session()` classmethod restores previous session from disk
- Auto-saves every 60 seconds (configurable via `BBQ_SAVE_INTERVAL`) in `process_temp_update()`
- Also saves after each user message in `handle_user_input()`
- Session files: Timestamped format `.bbq_session_YYYY-MM-DD_HHMMSS.json`
- Saves complete conversation history
- Preserves all temperature readings

**Session Management** (ai_pitmaster.py:636-771)
- `get_session_filename()` generates timestamped filenames
- `find_latest_session()` finds the most recent session file
- `get_session_age()` calculates age from filename timestamp
- `archive_old_sessions()` moves sessions > 48 hours to `.bbq_archive/`
- `generate_session_mailto()` creates mailto link for sharing session data
- `print_share_instructions()` displays instructions for sharing archived sessions
- Sessions < 48 hours automatically offered for restore on startup
- Older sessions archived to prevent directory clutter

### Key Data Flow

1. `rtl_433` subprocess captures JSON temperature data from 433MHz wireless thermometer
2. Data parsed and queued in `temp_reader_thread()` running in background thread
3. Main loop (`run()`) processes temperature updates via `_process_temp_data()`
4. `check_critical_conditions()` evaluates alert thresholds and sends SMS if needed
5. User inputs natural language messages via `handle_user_input()`
6. Messages contextualized with current temps/stats and sent to Claude
7. Display shows periodic temp updates based on `BBQ_DISPLAY_INTERVAL`

### Configuration

**Environment Variables**
- `ANTHROPIC_API_KEY` (required): API key for Claude
- `TXTBELT_KEY` (optional): TextBelt API key for SMS (defaults to free tier)
- `BBQ_PHONE` (optional): Phone number for SMS alerts
- `BBQ_SMS_COOLDOWN` (optional): Seconds between SMS alerts per type (default: 900)
- `BBQ_DISPLAY_INTERVAL` (optional): Seconds between temp displays (default: 120)
- `BBQ_PROACTIVE_INTERVAL` (optional): Seconds between proactive checks (default: 300)
- `BBQ_SAVE_INTERVAL` (optional): Seconds between auto-saves (default: 60)

**Runtime State**
- Interactive setup prompts for: meat type, weight, target pit temp, target meat temp
- No persistent configuration files - all state is in-memory only
- Conversation history stored in `messages` list
- Temperature history in `temp_history` deque
- Claude API receives full conversation history

### Hardware Dependencies

- RTL-SDR dongle (RTL2832U-based) for receiving 433MHz signals
- Compatible wireless thermometer (Thermopro TP12 tested, others via rtl_433)
- `rtl_433` binary must be installed and on PATH

## Testing Strategy

Tests in `tests/` directory using pytest:
- `test_ai_pitmaster.py`: Core logic (stall detection, SMS, critical conditions, model fitting)
- `test_main.py`: Application flow and integration testing
- `conftest.py`: Test fixtures and mock configuration

All external dependencies (subprocess, HTTP requests, Anthropic API) are mocked - no hardware required.

## Important Implementation Notes

- **POSIX-only**: Uses `select` module for non-blocking stdin (ai_pitmaster.py:~618-625)
- **Session persistence**: State auto-saved to timestamped files every 60s and after user messages; sessions < 48h auto-restore on startup, older sessions archived (ai_pitmaster.py:117-251, 636-771, main.py:775-833)
- **Optional dependencies**: SciPy is optional - ETA prediction gracefully disabled without it
- **Thread safety**: Temperature queue is thread-safe via `queue.Queue`; alert states managed in main thread only
- **Ambient temp parsing**: Extracts from rtl_433 weather station data when available (ai_pitmaster.py:~555-560)
- **Data sharing**: Users can generate mailto links to share archived sessions for analysis and improvement of the software