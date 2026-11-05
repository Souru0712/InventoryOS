"""Reading invoices and packaging with a vision model."""

# How long the Anthropic API may go without sending anything before the call is abandoned
# (T0.07). The SDK's default is 600 s, long enough to hold a worker for ten minutes.
ANTHROPIC_TIMEOUT_SECONDS = 60.0
