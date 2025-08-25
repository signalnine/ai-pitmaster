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

# Run the main application
python3 ai_pitmaster.py
```

### Dependencies
```bash
# Install Python dependencies
pip install -r requirements.txt
```

## Architecture Overview

The application is built around a single main class `ClaudeBBQConversation` in `ai_pitmaster.py` that orchestrates:

### Core Components
- **Temperature Reading**: Background thread running `rtl_433` to capture wireless thermometer data from Thermopro TP12 devices
- **Claude Integration**: Maintains conversation context with Anthropic's Claude API for cooking advice
- **SMS Alerting**: Optional SMS notifications via TextBelt API for critical temperature events
- **Mathematical Modeling**: Uses SciPy curve fitting to predict cook times with 5-parameter logistic curves
- **Stall Detection**: Implements Henderson's mathematical stall criterion using finite difference calculations

### Key Data Flow
1. `rtl_433` subprocess captures JSON temperature data from 433MHz wireless thermometer
2. Data parsed and queued in `temp_reader_thread()`
3. Main loop processes temperature updates, checks critical conditions, updates predictive models
4. User can input natural language messages that get contextualized with current temperature data and sent to Claude
5. Critical temperature events trigger SMS alerts with cooldown logic

### Configuration
- Environment variables: `ANTHROPIC_API_KEY` (required), `TXTBELT_KEY`, `BBQ_PHONE`, `BBQ_SMS_COOLDOWN`
- Interactive setup prompts for meat type, weight, target temperatures
- No persistent configuration files - all state is runtime only

### Hardware Dependencies
- RTL-SDR dongle (RTL2832U-based) for receiving 433MHz signals
- Compatible wireless thermometer (Thermopro TP12 tested, others supported by rtl_433)
- `rtl_433` binary must be installed and on PATH

## Testing Strategy

Tests are located in `tests/` directory using pytest:
- `test_ai_pitmaster.py`: Core logic testing (stall detection, SMS, critical conditions)  
- `test_main.py`: Application flow and integration testing
- `conftest.py`: Test fixtures and configuration

Mock external dependencies (subprocess, API calls) in tests rather than requiring actual hardware.

## Important Notes

- Application is designed for POSIX systems (uses `select` for non-blocking stdin)
- Context is not persistent - conversation history lost on restart
- SciPy dependency is optional - ETA prediction gracefully degrades without it
- Thread safety considerations for temperature data queue and alert state management