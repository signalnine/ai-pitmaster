import pytest
import sys
from unittest.mock import patch, Mock

import ai_pitmaster


@patch('ai_pitmaster.ClaudeBBQConversation')
@patch('builtins.input')
@patch('os.getenv')
def test_main_function(mock_getenv, mock_input, mock_convo):
    """Test the main function with mocked inputs"""
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
    mock_convo.assert_called_once_with(
        'test-key',  # api_key
        225,         # target_pit
        203,         # target_meat
        'brisket',   # meat_type
        12.0,        # weight
        '+15555551234'  # phone
    )


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