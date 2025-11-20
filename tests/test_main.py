import pytest
import sys
from unittest.mock import patch, Mock

import ai_pitmaster


@patch('os.path.exists')
@patch('ai_pitmaster.ClaudeBBQConversation')
@patch('builtins.input')
@patch('os.getenv')
def test_main_function(mock_getenv, mock_input, mock_convo, mock_exists):
    """Test the main function with mocked inputs"""
    # Mock session file doesn't exist
    mock_exists.return_value = False

    # Mock environment variables
    def getenv_side_effect(key, default=None):
        if key == 'ANTHROPIC_API_KEY':
            return 'test-key'
        elif key == 'BBQ_PHONE':
            return '+15555551234'
        return default
    mock_getenv.side_effect = getenv_side_effect

    # Mock user inputs
    mock_input.side_effect = ['brisket', '12', '225', '203']

    # Mock the conversation run method
    mock_convo_instance = Mock()
    mock_convo.return_value = mock_convo_instance

    # Call main function
    ai_pitmaster.main()

    # Verify ClaudeBBQConversation was called with correct parameters
    # Note: session_file parameter is now included with timestamped filename
    call_args = mock_convo.call_args
    assert call_args[0] == ('test-key', 225, 203, 'brisket', 12.0, '+15555551234')
    assert 'session_file' in call_args[1]
    assert call_args[1]['session_file'].startswith('.bbq_session_')
    assert call_args[1]['session_file'].endswith('.json')


@patch('builtins.print')
@patch('os.getenv')
def test_main_missing_api_key(mock_getenv, mock_print):
    """Test main function when API key is missing"""
    # Mock environment variables to return None for API key
    def getenv_side_effect(key, default=None):
        if key == 'ANTHROPIC_API_KEY':
            return None
        return default
    mock_getenv.side_effect = getenv_side_effect
    
    # Verify that SystemExit is raised
    with pytest.raises(SystemExit):
        ai_pitmaster.main()
    
    # Verify error message was printed
    mock_print.assert_called_with("Set ANTHROPIC_API_KEY env var")