"""Canonical tool definitions for the server concierge bot.

Single source of truth shared by:
  - bot.py             → OpenAI function-calling format (OpenRouter fallback path)
  - concierge_server.py → MCP tool format (claude CLI primary path)

Each entry: name, description, parameters (JSON schema), func (callable in tools.py).
Add or change a tool here once; all consumers stay in sync.
"""
from tools import (
    get_agent_status,
    get_plant_status,
    get_yopflix_status,
    get_system_health,
    get_cron_schedule,
    get_agent_logs,
    run_travel_agent,
    get_travel_report,
    water_plant,
    water_plants,
    add_plant,
    update_plant,
    set_plant_frequency,
    remove_plant,
    research_plant_watering,
    research_plant_sunlight,
    save_recipe,
    get_plant,
    get_all_plants,
)

_EMPTY = {"type": "object", "properties": {}, "required": []}

SPECS = [
    {
        "name": "get_agent_status",
        "description": "Get the last run status of all server agents (daily-briefing, news-briefing, security-audit, travel-agent).",
        "parameters": _EMPTY,
        "func": get_agent_status,
    },
    {
        "name": "get_plant_status",
        "description": "Get the watering schedule for all tracked plants including next watering date and overdue flags.",
        "parameters": _EMPTY,
        "func": get_plant_status,
    },
    {
        "name": "get_yopflix_status",
        "description": "Get the yopflix/seedbox status: enabled services, running Docker containers, and disk usage.",
        "parameters": _EMPTY,
        "func": get_yopflix_status,
    },
    {
        "name": "get_system_health",
        "description": "Get server system health: CPU usage, RAM usage, and uptime.",
        "parameters": _EMPTY,
        "func": get_system_health,
    },
    {
        "name": "get_cron_schedule",
        "description": "Get the cron schedule for all agents showing when they run (in CEST).",
        "parameters": _EMPTY,
        "func": get_cron_schedule,
    },
    {
        "name": "get_agent_logs",
        "description": "Get recent log output. Optionally filter by agent name.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Agent name to filter by (e.g. 'daily-briefing'). Leave empty for all logs.",
                }
            },
            "required": [],
        },
        "func": get_agent_logs,
    },
    {
        "name": "run_travel_agent",
        "description": (
            "Launch the travel agent in the background to research or plan a trip. "
            "Use mode='search' to find flights, hotels, and activities. "
            "Use mode='plan' when the user already has flights and accommodation booked and wants a day-by-day itinerary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "destination": {"type": "string", "description": "Destination city or country (e.g. 'Barcelona', 'Japan')."},
                "checkin": {"type": "string", "description": "Check-in / arrival date in YYYY-MM-DD format."},
                "checkout": {"type": "string", "description": "Check-out / departure date in YYYY-MM-DD format."},
                "mode": {
                    "type": "string",
                    "enum": ["search", "plan"],
                    "description": "search=find flights+hotels, plan=itinerary from existing bookings. Default: search.",
                },
                "origin": {"type": "string", "description": "Departure city for search mode (e.g. 'Dublin', 'Amsterdam')."},
                "flights": {"type": "string", "description": "Plan mode only: existing flight details as free text."},
                "hotel": {"type": "string", "description": "Plan mode only: existing hotel booking as free text."},
            },
            "required": ["destination", "checkin", "checkout"],
        },
        "func": run_travel_agent,
    },
    {
        "name": "get_travel_report",
        "description": "Check whether the latest travel research report is ready and return its filename and size.",
        "parameters": _EMPTY,
        "func": get_travel_report,
    },
    {
        "name": "water_plants",
        "description": "Record that all plants in a given location were watered today. Use when the user says they watered all indoor or all outdoor plants.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "enum": ["indoor", "outdoor"],
                    "description": "Location of the plants to mark as watered ('indoor' or 'outdoor').",
                }
            },
            "required": ["location"],
        },
        "func": water_plants,
    },
    {
        "name": "water_plant",
        "description": "Record that a plant was watered today. Use when the user says they watered a plant.",
        "parameters": {
            "type": "object",
            "properties": {
                "plant_name": {"type": "string", "description": "Name of the plant (e.g. 'Monstera')."}
            },
            "required": ["plant_name"],
        },
        "func": water_plant,
    },
    {
        "name": "add_plant",
        "description": "Add a new plant to the watering tracker. Use when the user wants to track a new plant.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the plant (e.g. 'Monstera Deliciosa')."},
                "frequency_days": {"type": "integer", "description": "How often to water the plant in days (e.g. 7 for weekly)."},
                "location": {
                    "type": "string",
                    "enum": ["indoor", "outdoor"],
                    "description": "Whether the plant is indoors or outdoors. Default: indoor.",
                },
                "sunlight": {
                    "type": "string",
                    "enum": ["full sun", "partial shade", "shade"],
                    "description": "Sunlight requirements. Call research_plant_sunlight first if unsure.",
                },
            },
            "required": ["name", "frequency_days"],
        },
        "func": add_plant,
    },
    {
        "name": "update_plant",
        "description": "Update a plant's location, watering frequency, or sunlight requirements. Use when the user says a plant is indoor/outdoor, wants to change watering frequency, or specify sunlight needs.",
        "parameters": {
            "type": "object",
            "properties": {
                "plant_name": {"type": "string", "description": "Name of the plant to update (e.g. 'Gazania')."},
                "location": {"type": "string", "enum": ["indoor", "outdoor"], "description": "New location for the plant."},
                "frequency_days": {"type": "integer", "description": "New watering frequency in days."},
                "sunlight": {
                    "type": "string",
                    "enum": ["full sun", "partial shade", "shade"],
                    "description": "New sunlight requirements for the plant.",
                },
            },
            "required": ["plant_name"],
        },
        "func": update_plant,
    },
    {
        "name": "set_plant_frequency",
        "description": "Set a plant's baseline watering frequency in days (1-30). Weather is folded into the effective schedule automatically. Prefer this over update_plant when the user wants to change how often a plant is watered.",
        "parameters": {
            "type": "object",
            "properties": {
                "plant_name": {"type": "string", "description": "Name of the plant (e.g. 'Lantana')."},
                "frequency_days": {"type": "integer", "description": "New baseline watering interval in days (1-30)."},
                "reason": {"type": "string", "description": "Short reason for the change (optional)."},
            },
            "required": ["plant_name", "frequency_days"],
        },
        "func": set_plant_frequency,
    },
    {
        "name": "research_plant_sunlight",
        "description": "Look up sunlight requirements for a plant. Returns 'full sun', 'partial shade', or 'shade'. Call before add_plant or update_plant when sunlight is unknown.",
        "parameters": {
            "type": "object",
            "properties": {
                "plant_name": {"type": "string", "description": "Name of the plant to research (e.g. 'Monstera Deliciosa')."}
            },
            "required": ["plant_name"],
        },
        "func": research_plant_sunlight,
    },
    {
        "name": "research_plant_watering",
        "description": "Look up the recommended watering frequency for a plant using web search. Call this before add_plant when the user hasn't specified how often to water.",
        "parameters": {
            "type": "object",
            "properties": {
                "plant_name": {"type": "string", "description": "Name of the plant to research (e.g. 'Monstera Deliciosa')."}
            },
            "required": ["plant_name"],
        },
        "func": research_plant_watering,
    },
    {
        "name": "save_recipe",
        "description": "Save a recipe URL to Mealie. Use when the user sends a recipe link or asks to save a recipe.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The recipe URL."}
            },
            "required": ["url"],
        },
        "func": save_recipe,
    },
    {
        "name": "get_plant",
        "description": "Look up a single plant by name (exact or substring match). Returns plant details or null if not found.",
        "parameters": {
            "type": "object",
            "properties": {
                "plant_name": {"type": "string", "description": "Name of the plant to look up (e.g. 'Monstera')."}
            },
            "required": ["plant_name"],
        },
        "func": get_plant,
    },
    {
        "name": "get_all_plants",
        "description": "Get the full list of all tracked plants with their details (name, location, sunlight, watering frequency, last watered, last assessment).",
        "parameters": _EMPTY,
        "func": get_all_plants,
    },
    {
        "name": "remove_plant",
        "description": "Remove a plant from the watering tracker. Use when the user says a plant has died or they no longer want to track it.",
        "parameters": {
            "type": "object",
            "properties": {
                "plant_name": {"type": "string", "description": "Name of the plant to remove (e.g. 'Monstera')."}
            },
            "required": ["plant_name"],
        },
        "func": remove_plant,
    },
]


def openai_tools() -> list[dict]:
    """OpenAI function-calling format for the OpenRouter fallback path."""
    return [
        {
            "type": "function",
            "function": {"name": s["name"], "description": s["description"], "parameters": s["parameters"]},
        }
        for s in SPECS
    ]


def mcp_tools() -> list[dict]:
    """MCP tool format for the concierge MCP server."""
    return [
        {"name": s["name"], "description": s["description"], "inputSchema": s["parameters"]}
        for s in SPECS
    ]


def func_map() -> dict:
    """Map tool name → callable for dispatch."""
    return {s["name"]: s["func"] for s in SPECS}
