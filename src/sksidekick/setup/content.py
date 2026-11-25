"""Fixed copy for the setup page. The honest labels are the product: change them only with the spec."""

from __future__ import annotations

GATEWAY_URL_ENV = "SKSIDEKICK_GATEWAY_URL"
GATEWAY_KEY_ENV = "SKSIDEKICK_GATEWAY_KEY"
SHARED_DISABLED = "Available when we invite you (a seat)"

TIERS = (
    {
        "id": "private",
        "title": "Private",
        "label": (
            "A model running on hardware you control. Nothing readable leaves your hardware. "
            "We hold only encrypted memory we cannot open."
        ),
        "needs": ["base_url", "model"],
    },
    {
        "id": "own",
        "title": "Own account",
        "label": (
            "You bring your own cloud model account. We still cannot read anything; the provider "
            "sees your messages under your own terms with that provider."
        ),
        "needs": ["base_url", "model", "api_key"],
    },
    {
        "id": "shared",
        "title": "Shared gateway",
        "label": (
            "Our gateway, with message logging off for sidekicks. This is a promise, not a lock, "
            "and the model provider sees your messages. The provider sees our account, never your "
            "name, email or IP; we can see how much your sidekick uses and when, never what it says."
        ),
        "needs": [GATEWAY_URL_ENV, GATEWAY_KEY_ENV],
    },
)

# Piper voices the personality samples were made with.
VOICES = {"female": "en_US-amy-medium", "male": "en_US-ryan-medium"}

# Env names read by the nctalk plugin (from its README). Task 6's image test greps the plugin for each.
NCTALK_ENV_NAMES = ("NEXTCLOUD_TALK_SERVER_URL", "NEXTCLOUD_TALK_USERNAME", "NEXTCLOUD_TALK_APP_PASSWORD")
NCTALK_DISABLED = "Comes with an invite to our Nextcloud"
# Field names that hold a secret: never echoed back.
SECRET_FIELDS = frozenset({"NEXTCLOUD_TALK_APP_PASSWORD", "TELEGRAM_BOT_TOKEN"})

CHANNELS = (
    {
        "id": "nextcloud-talk",
        "title": "Nextcloud Talk",
        "label": "Stays inside our own server.",
        "fields": list(NCTALK_ENV_NAMES),
    },
    {
        "id": "telegram",
        "title": "Telegram",
        "label": "Telegram also sees the conversation.",
        "fields": ["TELEGRAM_BOT_TOKEN"],
    },
)
