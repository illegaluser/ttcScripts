# Zero-Touch QA Agent Documentation

## Overview

The Zero-Touch QA Agent is an autonomous testing system that converts natural language requirements (SRS) into executable browser tests using AI and Playwright. It can automatically execute tests, recover from failures, and generate regression test scripts.

## Key Features

1. **Natural Language to Test Conversion**: Converts SRS (Software Requirements Specification) text into executable Playwright test scenarios
2. **Self-Healing**: Automatically recovers from test failures using multiple strategies:
   - Fallback targets
   - Candidate search
   - LLM-based healing
3. **Multi-browser Support**: Uses Playwright for cross-browser testing
4. **Regression Script Generation**: Creates standalone Playwright scripts for future use
5. **Detailed Reporting**: Generates HTML reports with screenshots and execution details

## Architecture

### Core Components

1. **IntentTarget**: Data model for representing UI elements with various attributes (role, name, text, etc.)
2. **LocatorResolver**: Resolves UI element locators using multiple strategies (role+name, text, label, etc.)
3. **ZeroTouchAgent**: Main class that orchestrates the entire testing process

### Main Execution Flow

1. **Planning Phase**: 
   - Converts SRS text to JSON test scenario using LLM
   - Stores scenario in `test_scenario.json`

2. **Execution Phase**:
   - Launches browser with stealth settings
   - Executes each step in the scenario
   - Handles tab switching when new pages are opened
   - Logs execution details

3. **Healing Phase**:
   - When step fails, attempts multiple recovery strategies:
     - Fallback targets (predefined alternatives)
     - Candidate search (find similar elements)
     - LLM healing (ask AI for solutions)

4. **Reporting Phase**:
   - Generates HTML report with execution results
   - Saves screenshots for each step
   - Creates regression test script

## Configuration

### Environment Variables

- `OLLAMA_HOST`: Ollama server address (default: `http://host.docker.internal:11434`)
- `MODEL_NAME`: LLM model name (default: `qwen3-coder:30b`)
- `HEADLESS`: Run browser in headless mode (default: `true`)
- `SLOW_MO_MS`: Slow motion delay (default: `500`)
- `DEFAULT_TIMEOUT_MS`: Default timeout (default: `30000`)
- `FAST_TIMEOUT_MS`: Fast timeout for element detection (default: `2000`)
- `HEAL_MODE`: Healing mode (default: `on`)
- `MAX_HEAL_ATTEMPTS`: Maximum healing attempts (default: `2`)
- `CANDIDATE_TOP_N`: Number of candidates to consider (default: `8`)

## Supported Actions

1. `navigate`: Navigate to URL
2. `click`: Click on element
3. `double_click`: Double-click on element
4. `hover`: Hover over element
5. `fill`: Fill text into input field
6. `select_option`: Select option from dropdown
7. `press_sequential`: Type text character by character
8. `check`: Check checkbox
9. `press_key`: Press keyboard key
10. `scroll`: Scroll element into view
11. `assert_text`: Verify text content
12. `assert_visible`: Verify element visibility
13. `go_back`: Go back in browser history
14. `go_forward`: Go forward in browser history
15. `wait`: Wait for specified time

## Usage

```bash
python autonomous_qa.py --url "https://example.com" --srs_file "requirements.txt" --out "./results"
```

## Files Generated

1. `test_scenario.json`: Original test scenario from LLM
2. `test_scenario.healed.json`: Scenario after healing attempts
3. `run_log.jsonl`: Execution log with timestamps
4. `index.html`: HTML report with results and screenshots
5. `regression_test.py`: Standalone Playwright regression script

## Key Methods

### `plan_scenario()`
Converts SRS text to test scenario using LLM

### `execute(scenario)`
Executes the test scenario with healing capabilities

### `_heal_step()`
Attempts multiple healing strategies when a step fails

### `generate_regression_script()`
Creates a standalone Playwright script for regression testing

## Error Handling

The system implements comprehensive error handling:
- Element resolution failures
- Timeout errors
- Assertion failures
- Browser navigation issues
- Healing attempts with fallback strategies

## Self-Healing Strategies

1. **Fallback Targets**: Predefined alternative targets for the same action
2. **Candidate Search**: Finds similar elements on the page using accessibility tree
3. **LLM Healing**: Asks the LLM to suggest solutions based on the error and page context

## Stealth Features

- Removes `navigator.webdriver` property to avoid bot detection
- Sets appropriate user agent and locale
- Uses realistic viewport sizes
- Adds extra HTTP headers to mimic real user behavior

## Exit Codes

- `0`: All tests passed
- `1`: Test failed (with detailed error reporting in logs)