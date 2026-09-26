<?php

return [
    'wp_url' => env('AUCTION_WP_URL', 'http://localhost:9000'),

    'increment' => (int) env('AUCTION_INCREMENT', 5000),
    'currency' => env('AUCTION_CURRENCY', 'KES'),

    // When to spawn the sniper before close. The process starts here;
    // its internal modes decide when it actually starts firing.
    'endgame_lead' => (int) env('AUCTION_ENDGAME_LEAD', 300),

    // Sniper process
    'sniper_script' => base_path('app/Scripts/snipe.py'),
    'sniper_python' => env('AUCTION_PYTHON', 'python3'),
    'sniper_log_dir' => storage_path('logs'),

    // Strategy tuning — passed through to the Python process
    // as command-line args at spawn time.
    'poll_quiet_ms' => (int) env('AUCTION_POLL_QUIET_MS', 200),
    'poll_contest_ms' => (int) env('AUCTION_POLL_CONTEST_MS', 50),
    'contest_window_s' => (int) env('AUCTION_CONTEST_WINDOW_S', 30),
    'tail_safety_ms' => (int) env('AUCTION_TAIL_SAFETY_MS', 150),
    'tail_min_ms' => (int) env('AUCTION_TAIL_MIN_MS', 300),

    // Cookie validation
    'probe_vehicle_wp_id' => env('AUCTION_PROBE_VEHICLE_WP_ID'),

    // Monitoring cadence (the outer Laravel poll, not the sniper's)
    'roster_interval' => 300,
    'cadence' => [
        1800 => 300,
        300 => 60,
        0 => 20,
    ],
    'jitter' => 0.20,
];