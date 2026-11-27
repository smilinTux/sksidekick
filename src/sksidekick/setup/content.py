"""Fixed copy for the setup page. The honest labels are the product: change them only with the spec."""

from __future__ import annotations

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
        "needs": [],
    },
)

# Piper voices the personality samples were made with.
VOICES = {"female": "en_US-amy-medium", "male": "en_US-ryan-medium"}

# Env names read by the nctalk plugin (from its README). Task 6's image test greps the plugin for each.
NCTALK_ENV_NAMES = ("NEXTCLOUD_TALK_SERVER_URL", "NEXTCLOUD_TALK_USERNAME", "NEXTCLOUD_TALK_APP_PASSWORD")
NCTALK_DISABLED = "Comes with an invite to our Nextcloud"
# Hermes answers on Telegram only to the users listed here; without it a message from the owner gets a
# pairing prompt or nothing (the fork's gateway/run_startup.py).
TELEGRAM_ALLOWED_USERS = "TELEGRAM_ALLOWED_USERS"
# Fields that take digits only (a numeric id), with the error shown otherwise.
DIGIT_FIELDS = {TELEGRAM_ALLOWED_USERS: "enter your Telegram user id, digits only"}
# One line of help under a field, where the page needs one.
FIELD_HELP = {
    TELEGRAM_ALLOWED_USERS: (
        "Only this id can talk to your sidekick on Telegram. To find yours, message @userinfobot in Telegram: "
        "it replies with your id."
    ),
}
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
        "fields": ["TELEGRAM_BOT_TOKEN", TELEGRAM_ALLOWED_USERS],
    },
)


def _p(pid: str, title: str, tagline: str, description: str, samples: list[str]) -> dict:
    return {
        "id": pid, "title": title, "tagline": tagline, "description": description, "samples": samples,
        "audio": {v: f"static/audio/{pid}-{v}.mp3" for v in ("female", "male")},
    }


# The four personality types, as approved in the design. Tone, pace and how much it offers
# unprompted only: none of them changes what the sidekick is allowed to do.
PERSONALITIES = (
    _p("companion", "Companion", "warm and steady",
       "Remembers the small things, checks in, keeps the day light when it is hard. Offers help but never pushes. "
       "Best for people who want someone in their corner.",
       ["Morning. You said yesterday was rough, so I kept today light.",
        "Your mom's birthday is Sunday. Want me to remind you Friday so there is time for a card?",
        "No rush on this one. I'm here when you want to pick it back up."]),
    _p("assistant", "Assistant", "sharp and brief",
       "Leads with the answer, lists, deadlines and the next step. Drafts first, asks second. "
       "Best for busy people who want things handled.",
       ["Three things today. Reply to Maria by noon, the invoice is due Friday, your flight is confirmed.",
        "I drafted the reply. Say send, or tell me what to change.",
        "Done. Next up is the insurance form, about ten minutes."]),
    _p("creative", "Creative", "playful and curious",
       "Riffs on ideas, jokes around, suggests the unexpected. Still says plainly when something is a guess. "
       "Best for people who want a thinking and making partner.",
       ["Okay, wild idea. What if the party is a treasure hunt built on inside jokes?",
        "I have five versions of that headline. Two are terrible on purpose.",
        "Want the safe option or the fun one? I can do both."]),
    _p("mentor", "Mentor", "calm and patient",
       "Asks good questions, slows things down, helps the person decide rather than deciding for them. "
       "Best for learning, planning and hard choices.",
       ["Let's slow down for a second. What outcome do you actually want?",
        "Say it in one sentence, and we'll write back from there.",
        "You've done harder things than this. What worked last time?"]),
)
