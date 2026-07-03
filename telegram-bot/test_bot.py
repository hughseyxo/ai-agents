import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from bot import start, handle_message, _analyze_plant_image, handle_photo, _identify_plant_from_image, _build_identification_context, _build_common_name_lookup


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
async def test_antigravity_backend_reply_is_sent_directly(mocker):
    """When the Antigravity backend returns a reply, it's sent and Claude is skipped."""
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    mocker.patch("bot.ask_antigravity", return_value="Your plants are happy.")
    mock_claude = mocker.patch("bot.ask_claude")
    update = MagicMock()
    update.effective_user.id = 1703830475
    update.message.text = "how are my plants?"
    update.effective_chat.id = 123
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    await handle_message(update, context)

    update.message.reply_text.assert_called_once_with("Your plants are happy.")
    mock_claude.assert_not_called()


@pytest.mark.asyncio
async def test_antigravity_none_falls_back_to_claude(mocker):
    """Antigravity returns None → Claude is called and its reply is sent."""
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    mocker.patch("bot.ask_antigravity", return_value=None)
    mock_claude = mocker.patch("bot.ask_claude", return_value="Hi from Claude.")
    update = MagicMock()
    update.effective_user.id = 1703830475
    update.message.text = "hello"
    update.effective_chat.id = 123
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    await handle_message(update, context)

    mock_claude.assert_called_once()
    update.message.reply_text.assert_called_once_with("Hi from Claude.")


@pytest.mark.asyncio
async def test_both_backends_none_sends_unavailable_message(mocker):
    """Both Antigravity and Claude return None → user gets unavailable message."""
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    mocker.patch("bot.ask_antigravity", return_value=None)
    mocker.patch("bot.ask_claude", return_value=None)
    update = MagicMock()
    update.effective_user.id = 1703830475
    update.message.text = "status?"
    update.effective_chat.id = 123
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    await handle_message(update, context)

    update.message.reply_text.assert_called_once()
    assert "unavailable" in update.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_antigravity_exception_falls_back_to_claude(mocker):
    """An exception in the Antigravity backend is logged and Claude takes over."""
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    mocker.patch("bot.ask_antigravity", side_effect=RuntimeError("boom"))
    mock_claude = mocker.patch("bot.ask_claude", return_value="Claude saves the day.")
    update = MagicMock()
    update.effective_user.id = 1703830475
    update.message.text = "hello"
    update.effective_chat.id = 123
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    await handle_message(update, context)

    mock_claude.assert_called_once()
    update.message.reply_text.assert_called_once_with("Claude saves the day.")


# ---------------------------------------------------------------------------
# _identify_plant_from_image helpers
# ---------------------------------------------------------------------------

def test_identify_plant_partial_name_match(mocker):
    """Model returns 'Monstera' — should match 'Monstera Deliciosa'."""
    plants = [{"name": "Monstera Deliciosa", "location": "indoor", "last_watered": "2026-05-20", "frequency_days": 10}]
    mocker.patch("bot.assess_image", return_value="Monstera")
    mocker.patch("bot._build_identification_context", return_value="- Monstera Deliciosa: glossy leaves")
    mocker.patch("bot._build_common_name_lookup", return_value={})

    result = _identify_plant_from_image(b"img", plants)
    assert result is not None
    assert result["name"] == "Monstera Deliciosa"


def test_identify_plant_common_name_match(mocker):
    """Model returns 'snake plant' — should match 'Dracaena Trifasciata'."""
    dracaena = {"name": "Dracaena Trifasciata", "location": "indoor", "last_watered": "2026-05-20", "frequency_days": 14}
    plants = [dracaena]
    mocker.patch("bot.assess_image", return_value="snake plant")
    mocker.patch("bot._build_identification_context", return_value="- Dracaena Trifasciata (also: snake plant): sword-like leaves")
    mocker.patch("bot._build_common_name_lookup", return_value={"snake plant": dracaena, "mother-in-law's tongue": dracaena})

    result = _identify_plant_from_image(b"img", plants)
    assert result is not None
    assert result["name"] == "Dracaena Trifasciata"


def test_identify_plant_returns_none_when_cli_fails(mocker):
    """assess_image returns None (CLI failure) → identification returns None."""
    plants = [{"name": "Monstera Deliciosa", "location": "indoor", "last_watered": "2026-05-20", "frequency_days": 10}]
    mocker.patch("bot.assess_image", return_value=None)
    mocker.patch("bot._build_identification_context", return_value="- Monstera Deliciosa: glossy leaves")
    mocker.patch("bot._build_common_name_lookup", return_value={})

    assert _identify_plant_from_image(b"img", plants) is None


def test_build_common_name_lookup_parses_aliases(mocker):
    """_build_common_name_lookup returns a dict keyed by lowercase alias."""
    dracaena = {"name": "Dracaena Trifasciata", "location": "indoor", "last_watered": "2026-05-20", "frequency_days": 14}
    mocker.patch("bot._load_species_context", return_value=(
        "## Dracaena Trifasciata (Snake Plant)\n\n"
        "**Also known as:** snake plant, mother-in-law's tongue\n\n"
        "**Healthy indicators:** Upright sword-like leaves."
    ))
    lookup = _build_common_name_lookup([dracaena])
    assert "snake plant" in lookup
    assert lookup["snake plant"]["name"] == "Dracaena Trifasciata"
    assert "mother-in-law's tongue" in lookup


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


def test_analyze_plant_image_returns_assessment_text(mocker):
    """Non-JSON reply falls through to plain-text display."""
    mocker.patch("bot.assess_image", return_value="Leaves look healthy.")

    result, parsed = _analyze_plant_image(FAKE_IMAGE, FAKE_PLANT)

    assert "healthy" in result.lower()
    assert parsed is None


def test_analyze_plant_image_parses_json_assessment(mocker):
    """Structured JSON reply is parsed into a dict and a formatted display."""
    assessment = {
        "status": "Healthy",
        "summary": "Vibrant and turgid.",
        "observations": ["glossy leaves"],
        "watering_recommendation": "on_schedule",
        "frequency_suggestion": None,
        "profile_notes": "### 2026-06-02 — Healthy\nLooks great.",
    }
    mocker.patch("bot.assess_image", return_value=json.dumps(assessment))

    result, parsed = _analyze_plant_image(FAKE_IMAGE, FAKE_PLANT)

    assert parsed == assessment
    assert "Healthy" in result
    assert "Vibrant" in result


def test_analyze_plant_image_returns_unavailable_when_cli_fails(mocker):
    mocker.patch("bot.assess_image", return_value=None)

    result, parsed = _analyze_plant_image(FAKE_IMAGE, FAKE_PLANT)

    assert "unavailable" in result.lower()
    assert parsed is None


def test_analyze_plant_image_includes_plant_context_in_prompt(mocker):
    spy = mocker.patch("bot.assess_image", return_value="Looks good.")

    _analyze_plant_image(FAKE_IMAGE, FAKE_PLANT)

    # assess_image(path, system_prompt, user_text)
    user_text = spy.call_args.args[2]
    assert "Monstera" in user_text
    assert "indoor" in user_text


# ---------------------------------------------------------------------------
# handle_photo
# ---------------------------------------------------------------------------

def _make_photo_update(caption=None, user_id=1703830475):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = 123
    update.message.caption = caption
    update.message.reply_text = AsyncMock()
    mock_photo = MagicMock()
    mock_photo.file_id = "file123"
    update.message.photo = [mock_photo]
    return update


@pytest.mark.asyncio
async def test_handle_photo_no_caption_triggers_visual_id(mocker):
    """No caption (e.g. subsequent photos in a media group) should trigger visual identification."""
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    fake_plant = {"name": "Monstera", "location": "indoor", "last_watered": "2026-05-28", "frequency_days": 10}
    mocker.patch("bot.get_all_plants", return_value=[fake_plant])
    mocker.patch("bot._identify_plant_from_image", return_value=fake_plant)
    mocker.patch("bot._analyze_plant_image", return_value=("Healthy.", None))
    mocker.patch("bot.save_plant_assessment", return_value="saved.")

    update = _make_photo_update(caption=None)
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    mock_file = AsyncMock()
    mock_file.download_to_memory = AsyncMock(side_effect=lambda buf: buf.write(b"fakejpeg"))
    context.bot.get_file = AsyncMock(return_value=mock_file)

    await handle_photo(update, context)

    update.message.reply_text.assert_called_once_with("**Monstera**\n\nHealthy.")


@pytest.mark.asyncio
async def test_handle_photo_llm_resolves_common_name(mocker):
    """'passion flower' doesn't substring-match 'Passiflora' — LLM fallback resolves it."""
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    fake_plant = {"name": "Passiflora", "location": "outdoor", "last_watered": "2026-05-20", "frequency_days": 5}
    mocker.patch("bot.get_plant", return_value=None)
    mocker.patch("bot._resolve_plant_name", return_value=fake_plant)
    mocker.patch("bot._analyze_plant_image", return_value=("Looks healthy.", None))
    mocker.patch("bot.save_plant_assessment", return_value="saved.")

    update = _make_photo_update(caption="passion flower")
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    mock_file = AsyncMock()
    mock_file.download_to_memory = AsyncMock(side_effect=lambda buf: buf.write(b"fakejpeg"))
    context.bot.get_file = AsyncMock(return_value=mock_file)

    await handle_photo(update, context)

    update.message.reply_text.assert_called_once_with("**Passiflora**\n\nLooks healthy.")


@pytest.mark.asyncio
async def test_handle_photo_unknown_plant_replies_with_known_list(mocker):
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    mocker.patch("bot.get_plant", return_value=None)
    mocker.patch("bot._resolve_plant_name", return_value=None)
    fake_plants = [
        {"name": "Monstera", "frequency_days": 7, "last_watered": "2026-05-20", "location": "indoor"},
        {"name": "Aloe", "frequency_days": 14, "last_watered": "2026-05-10", "location": "indoor"},
    ]
    mocker.patch("bot.get_all_plants", return_value=fake_plants)

    update = _make_photo_update(caption="cactus")
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    await handle_photo(update, context)

    reply = update.message.reply_text.call_args[0][0]
    assert "cactus" in reply.lower()
    assert "Monstera" in reply or "Aloe" in reply


@pytest.mark.asyncio
async def test_handle_photo_happy_path(mocker):
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    fake_plant = {"name": "Monstera", "location": "indoor", "last_watered": "2026-05-20", "frequency_days": 7}
    mocker.patch("bot.get_plant", return_value=fake_plant)
    mocker.patch("bot._analyze_plant_image", return_value=("Leaves look healthy.", None))
    mocker.patch("bot.save_plant_assessment", return_value="Monstera assessment saved.")

    update = _make_photo_update(caption="monstera")
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    mock_file = AsyncMock()
    mock_file.download_to_memory = AsyncMock(side_effect=lambda buf: buf.write(b"fakejpeg"))
    context.bot.get_file = AsyncMock(return_value=mock_file)

    await handle_photo(update, context)

    update.message.reply_text.assert_called_once_with("**Monstera**\n\nLeaves look healthy.")


@pytest.mark.asyncio
async def test_handle_photo_assess_caption_uses_visual_id(mocker):
    """Caption 'assess' triggers visual identification then runs full assessment."""
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    fake_plant = {"name": "Monstera", "location": "indoor", "last_watered": "2026-05-28", "frequency_days": 10}
    mocker.patch("bot.get_all_plants", return_value=[fake_plant])
    mocker.patch("bot._identify_plant_from_image", return_value=fake_plant)
    mocker.patch("bot._analyze_plant_image", return_value=("Looks healthy.", None))
    mocker.patch("bot.save_plant_assessment", return_value="Monstera assessment saved.")

    update = _make_photo_update(caption="assess")
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    mock_file = AsyncMock()
    mock_file.download_to_memory = AsyncMock(side_effect=lambda buf: buf.write(b"fakejpeg"))
    context.bot.get_file = AsyncMock(return_value=mock_file)

    await handle_photo(update, context)

    update.message.reply_text.assert_called_once_with("**Monstera**\n\nLooks healthy.")


@pytest.mark.asyncio
async def test_handle_photo_assess_caption_identification_fails(mocker):
    """When visual ID returns None, bot asks user to name the plant."""
    mocker.patch("bot.ALLOWED_USER_ID", "1703830475")
    fake_plant = {"name": "Monstera", "location": "indoor", "last_watered": "2026-05-28", "frequency_days": 10}
    mocker.patch("bot.get_all_plants", return_value=[fake_plant])
    mocker.patch("bot._identify_plant_from_image", return_value=None)

    update = _make_photo_update(caption="assess")
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    mock_file = AsyncMock()
    mock_file.download_to_memory = AsyncMock(side_effect=lambda buf: buf.write(b"fakejpeg"))
    context.bot.get_file = AsyncMock(return_value=mock_file)

    await handle_photo(update, context)

    reply = update.message.reply_text.call_args[0][0].lower()
    assert "identify" in reply or "couldn't" in reply


@pytest.mark.asyncio
async def test_handle_photo_unauthorized_user_ignored(mocker):
    mocker.patch("bot.ALLOWED_USER_ID", "999999")
    update = _make_photo_update(caption="monstera", user_id=111111)
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    await handle_photo(update, context)

    update.message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# main() startup
# ---------------------------------------------------------------------------

def test_main_refuses_to_start_without_allowed_user(monkeypatch, caplog):
    import bot as bot_mod
    monkeypatch.setattr(bot_mod, "TELEGRAM_TOKEN", "dummy-token")
    monkeypatch.setattr(bot_mod, "ALLOWED_USER_ID", "")
    called = {}
    monkeypatch.setattr(
        bot_mod, "ApplicationBuilder",
        lambda: (_ for _ in ()).throw(AssertionError("must not build app")),
    )
    bot_mod.main()  # should return early, never touching ApplicationBuilder
    assert "TELEGRAM_USER_ID" in caplog.text
