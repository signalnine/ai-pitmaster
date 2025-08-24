# AI Pitmaster - Project Context for Qwen

## Project Overview

This project, AI Pitmaster, is a Python application that combines hardware (an RTL-SDR dongle) with a large language model (Anthropic's Claude) to provide real-time, intelligent assistance for barbecue smoking. It reads temperature data from a wireless BBQ thermometer (like the Thermopro TP12) and uses Claude to offer cooking advice, predict finish times, and send SMS alerts for critical events.

### Core Technologies

- **Python 3**: The main programming language.
- **RTL-SDR**: A software-defined radio library (`rtl_433`) used to receive data from the wireless thermometer.
- **Anthropic Claude API**: For natural language conversation and expert BBQ advice.
- **TextBelt API**: For sending SMS alerts (optional).
- **SciPy**: Used for mathematical curve fitting to predict cook times (optional).
- **Pytest**: For unit testing.

### Key Features

- Reads temperature data from Thermopro TP12 (or similar) via RTL-SDR.
- Maintains a conversation with Claude about the cook, allowing natural language input.
- Sends SMS alerts for critical events (pit temp crashes/spikes, stall, nearing done, done).
- Tracks ambient temperature from nearby weather stations.
- Detects the meat stall using a mathematical model.
- Predicts Estimated Time of Arrival (ETA) for wrapping and finishing using logistic curve fitting.

## Hardware Requirements

- An RTL2832U-based USB SDR dongle (e.g., RTL-SDR.com v4, NooElec NESDR).
- A 433MHz wireless BBQ thermometer (e.g., Thermopro TP12).

## Setup and Running

### 1. Install `rtl_433`

This is required for reading the thermometer data.

- **Debian/Ubuntu:** `sudo apt install rtl-433`
- **macOS:** `brew install rtl_433`
- **From source:** Clone the `rtl_433` repository and build it.

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

Dependencies include:
- `anthropic`
- `requests`
- `scipy` (for ETA prediction)
- `pytest` (for tests)

### 3. Set Environment Variables

These are required for API access and optional SMS alerts.

```bash
export ANTHROPIC_API_KEY=sk-ant-...        # Required
export TXTBELT_KEY=your_textbelt_key      # Optional, for SMS
export BBQ_PHONE=+15555551234             # Optional, for SMS
```

### 4. Run the Application

```bash
python3 ai_pitmaster.py
```

The application will prompt for meat type, weight, and target temperatures. It then starts `rtl_433` in the background to monitor the thermometer.

#### Usage During the Cook

- Interact with Claude by typing messages into the console (e.g., "just added a chimney of kingsford", "wrapped in butcher paper").
- Claude will provide advice and the system will update with current temperatures.
- SMS alerts are sent automatically for predefined critical conditions.

## Testing

The project uses `pytest` for unit testing.

Run all tests:

```bash
# Via the test runner script
python3 run_tests.py

# Or directly with pytest
python3 -m pytest tests -v
```

Tests cover core logic like stall detection, SMS sending, critical condition checks, and the main application flow.

## Development Conventions

- **Python Style**: Standard Python conventions are followed. The code is reasonably structured with classes and methods.
- **Dependencies**: Managed via `requirements.txt`.
- **Testing**: Unit tests are located in the `tests/` directory, using `pytest`.
- **Configuration**: Uses environment variables for sensitive information (API keys).
- **Concurrency**: Uses threading to run the `rtl_433` subprocess and process its output without blocking the main user input loop.
- **Error Handling**: Includes basic error handling for API calls, subprocess management, and data parsing.
- **Extensibility**: The `ClaudeBBQConversation` class encapsulates the core logic, making it relatively modular.

## Known Limitations / Notes

- Conversation context is not persistent and will be lost if the process is killed.
- Stall detection is experimental.
- Ambient temperature comes from nearby weather stations, which might not be perfectly accurate.
- Relies on an external LLM (Claude), incurring API costs.