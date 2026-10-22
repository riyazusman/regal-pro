"""Domain constants for Regal Pro scheduling engine and business rules."""

# Regal Unlimited membership requires minimum 91 minutes between start times
UNLIMITED_GAP_MINS: int = 91

# Nominal fudge factor allowing a 5-minute overlap for credits/ads
FUDGE_FACTOR_MINS: int = 5

# Default movie runtime if duration is missing or 0
DEFAULT_MOVIE_DURATION: int = 120

# Default fallback travel estimates when pairwise matrix has no record
DEFAULT_DRIVE_MINS: int = 20
DEFAULT_DRIVE_MILES: int = 5

# Cutoff window before current time for filtering past shows on the current day
PAST_SHOW_CUTOFF_MINS: int = 30

# Path scoring weights
SCORE_MOVIE_WEIGHT: float = 250.0
SCORE_HOP_PENALTY: float = 40.0
SCORE_MILE_PENALTY: float = 2.0
SCORE_GAP_PENALTY: float = 0.1
