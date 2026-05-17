import sys
import asyncio
import os

# Add hermes-agent to path
sys.path.insert(0, "/home/cian/.hermes/hermes-agent")

from hermes_cli.runtime_provider import resolve_runtime_provider
from agent.auxiliary_client import resolve_provider_client

async def test_fallback_chain():
    print("Testing Hermes Fallback Architecture...\n")
    
    # Test Primary (Gemini OAuth)
    primary_model = "gemini-3-flash-preview"
    print(f"Checking Primary: google-gemini-cli with {primary_model}...")
    
    try:
        client, resolved_model = resolve_provider_client(
            provider="google-gemini-cli",
            model=primary_model,
            explicit_base_url="cloudcode-pa://google"
        )
        print(f"SUCCESS: Primary client initialized. Resolved model: {resolved_model}")
    except Exception as e:
        print(f"FAIL: Primary initialization failed: {e}")

    # Test Fallback (Anthropic via Claude Code)
    fallback_model = "claude-haiku-4-5"
    print(f"\nChecking Fallback: anthropic ({fallback_model})...")
    
    try:
        client, resolved_model = resolve_provider_client(
            provider="anthropic",
            model=fallback_model
        )
        print(f"SUCCESS: Fallback client initialized. Resolved model: {resolved_model}")
    except Exception as e:
        print(f"FAIL: Fallback initialization failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_fallback_chain())
