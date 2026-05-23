import subprocess
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from bot import start, handle_message, _call_gemini_fallback, _analyze_plant_image


# ---------------------------------------------------------------------------
# /start command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_command_mentions_concierge():
    update = MagicMock()
    update.effective_user.mention_html.return_value = "User"
    update.message.reply_html = AsyncMock()
    context = MagicMock()

    await start(update, context)

    update.message.reply_html.assert_called_once()
    args, _ = update.message.reply_html.call_args
    assert "concierge" in args[0].lower() or "server" in args[0].lower()


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unauthorized_user_is_ignored(mocker):
    mocker.patch("bot.ALLOWED_USER_ID", "999999")
    update = MagicMock()
    update.effective_user.id = 111111
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    await handle_message(update, context)

    update.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_authorized_user_gets_response(mocker):
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    update = MagicMock()
    update.effective_user.id = 1703830475
    update.message.text = "hello"
    update.effective_chat.id = 123
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    mock_client = mocker.patch("bot.client")
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(
        finish_reason="stop",
        message=MagicMock(content="Hi there!", tool_calls=None)
    )]
    mock_client.chat.completions.create.return_value = mock_response

    await handle_message(update, context)

    update.message.reply_text.assert_called_once_with("Hi there!")


# ---------------------------------------------------------------------------
# Tool-use loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_call_is_executed_and_result_sent_back(mocker):
    """LLM returns a tool_call → bot executes it → sends result back to LLM → replies."""
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    update = MagicMock()
    update.effective_user.id = 1703830475
    update.message.text = "how are the agents doing?"
    update.effective_chat.id = 123
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_abc"
    mock_tool_call.function.name = "get_agent_status"
    mock_tool_call.function.arguments = "{}"

    first_response = MagicMock()
    first_response.choices = [MagicMock(
        finish_reason="tool_calls",
        message=MagicMock(tool_calls=[mock_tool_call], content=None)
    )]

    second_response = MagicMock()
    second_response.choices = [MagicMock(
        finish_reason="stop",
        message=MagicMock(content="Agents look healthy.", tool_calls=None)
    )]

    mock_client = mocker.patch("bot.client")
    mock_client.chat.completions.create.side_effect = [first_response, second_response]

    mocker.patch("bot.TOOL_FUNCTIONS", {"get_agent_status": lambda: "daily-briefing: success"})

    await handle_message(update, context)

    assert mock_client.chat.completions.create.call_count == 2
    update.message.reply_text.assert_called_once_with("Agents look healthy.")


@pytest.mark.asyncio
async def test_tool_loop_stops_after_max_iterations(mocker):
    """If LLM keeps requesting tool calls, loop breaks after 3 iterations."""
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    update = MagicMock()
    update.effective_user.id = 1703830475
    update.message.text = "status"
    update.effective_chat.id = 123
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_x"
    mock_tool_call.function.name = "get_agent_status"
    mock_tool_call.function.arguments = "{}"

    tool_response = MagicMock()
    tool_response.choices = [MagicMock(
        finish_reason="tool_calls",
        message=MagicMock(tool_calls=[mock_tool_call], content=None)
    )]

    mock_client = mocker.patch("bot.client")
    mock_client.chat.completions.create.return_value = tool_response
    mocker.patch("bot.TOOL_FUNCTIONS", {"get_agent_status": lambda: "ok"})

    await handle_message(update, context)

    assert mock_client.chat.completions.create.call_count <= 3


@pytest.mark.asyncio
async def test_openrouter_error_returns_error_message(mocker):
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    update = MagicMock()
    update.effective_user.id = 1703830475
    update.message.text = "hello"
    update.effective_chat.id = 123
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    mock_client = mocker.patch("bot.client")
    mock_client.chat.completions.create.side_effect = Exception("API Error")

    await handle_message(update, context)

    update.message.reply_text.assert_called_once()
    assert "error" in update.message.reply_text.call_args[0][0].lower()


# ---------------------------------------------------------------------------
# Gemini CLI fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_openrouter_models_fail_triggers_gemini(mocker):
    """When all OpenRouter models raise APIError, Gemini CLI is called."""
    from openai import RateLimitError

    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    update = MagicMock()
    update.effective_user.id = 1703830475
    update.message.text = "how are the agents?"
    update.effective_chat.id = 123
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    mock_client = mocker.patch("bot.client")
    mock_client.chat.completions.create.side_effect = RateLimitError(
        message="rate limited", response=MagicMock(status_code=429, headers={}), body={}
    )

    mocker.patch("bot.TOOL_FUNCTIONS", {"get_agent_status": lambda: "all good"})
    mock_run = mocker.patch("bot.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="Agents all good.\n")

    await handle_message(update, context)

    mock_run.assert_called_once()
    cmd_args = mock_run.call_args[0][0]
    assert "gemini" in cmd_args[0]
    update.message.reply_text.assert_called_once_with("Agents all good.")


@pytest.mark.asyncio
async def test_gemini_fallback_failure_sends_unavailable_message(mocker):
    """If Gemini CLI also fails, user gets a clear unavailable message."""
    from openai import RateLimitError

    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    update = MagicMock()
    update.effective_user.id = 1703830475
    update.message.text = "status?"
    update.effective_chat.id = 123
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    mock_client = mocker.patch("bot.client")
    mock_client.chat.completions.create.side_effect = RateLimitError(
        message="rate limited", response=MagicMock(status_code=429, headers={}), body={}
    )

    mocker.patch("bot.TOOL_FUNCTIONS", {"get_agent_status": lambda: "all good"})
    mock_run = mocker.patch("bot.subprocess.run")
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")

    await handle_message(update, context)

    update.message.reply_text.assert_called_once()
    assert "unavailable" in update.message.reply_text.call_args[0][0].lower()


def test_call_gemini_fallback_builds_prompt_with_server_state(mocker):
    """_call_gemini_fallback includes tool results in the prompt passed to gemini."""
    mocker.patch("bot.TOOL_FUNCTIONS", {
        "get_agent_status": lambda: "daily-briefing: success",
        "get_system_health": lambda: "CPU: 5%",
    })

    mock_run = mocker.patch("bot.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="All good.\n")

    result = _call_gemini_fallback("how is everything?", "You are a concierge.")

    assert result == "All good."
    # cmd is ["gemini", "-y", "-p", <prompt>, "-o", "text"]
    prompt_passed = mock_run.call_args[0][0][3]
    assert "daily-briefing: success" in prompt_passed
    assert "CPU: 5%" in prompt_passed
    assert "how is everything?" in prompt_passed


# ---------------------------------------------------------------------------
# _analyze_plant_image
# ---------------------------------------------------------------------------

FAKE_PLANT = {
    "name": "Monstera",
    "location": "indoor",
    "last_watered": "2026-05-20",
    "frequency_days": 7,
}
FAKE_IMAGE = b"fakejpegbytes"


def test_analyze_plant_image_returns_assessment_on_first_model(mocker):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Leaves look healthy."))]
    mocker.patch("bot.client").chat.completions.create.return_value = mock_response

    result = _analyze_plant_image(FAKE_IMAGE, FAKE_PLANT)

    assert "healthy" in result.lower()


def test_analyze_plant_image_tries_second_model_on_failure(mocker):
    mock_client = mocker.patch("bot.client")
    good_response = MagicMock()
    good_response.choices = [MagicMock(message=MagicMock(content="Plant is fine."))]
    mock_client.chat.completions.create.side_effect = [
        Exception("model unavailable"),
        good_response,
    ]

    result = _analyze_plant_image(FAKE_IMAGE, FAKE_PLANT)

    assert "fine" in result.lower()
    assert mock_client.chat.completions.create.call_count == 2


def test_analyze_plant_image_falls_back_to_gemini_when_all_vision_models_fail(mocker):
    mock_client = mocker.patch("bot.client")
    mock_client.chat.completions.create.side_effect = Exception("all fail")

    mock_run = mocker.patch("bot.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="Gemini says: plant is dry.\n")

    result = _analyze_plant_image(FAKE_IMAGE, FAKE_PLANT)

    assert "dry" in result.lower()
    called_cmd = mock_run.call_args[0][0]
    assert "gemini" in called_cmd[0]
    assert any(arg.endswith(".jpg") for arg in called_cmd)


def test_analyze_plant_image_returns_error_when_all_backends_fail(mocker):
    mocker.patch("bot.client").chat.completions.create.side_effect = Exception("fail")
    mocker.patch("bot.subprocess.run").return_value = MagicMock(returncode=1, stdout="", stderr="err")

    result = _analyze_plant_image(FAKE_IMAGE, FAKE_PLANT)

    assert "unavailable" in result.lower()


def test_analyze_plant_image_includes_plant_context_in_prompt(mocker):
    mock_client = mocker.patch("bot.client")
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Looks good."))]
    mock_client.chat.completions.create.return_value = mock_response

    _analyze_plant_image(FAKE_IMAGE, FAKE_PLANT)

    call_kwargs = mock_client.chat.completions.create.call_args
    messages = call_kwargs[1].get("messages") or call_kwargs[0][1]
    user_content = str(messages)
    assert "Monstera" in user_content
    assert "indoor" in user_content
