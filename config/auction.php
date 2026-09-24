<?php

return [
    'wp_url' => env('AUCTION_WP_URL', 'http://localhost:8000'),

    'increment'    => (int) env('AUCTION_INCREMENT', 5000),
    'currency'     => env('AUCTION_CURRENCY', 'KES'),
    'endgame_lead' => (int) env('AUCTION_ENDGAME_LEAD', 120),

    'roster_interval' => 300,

    // Poll cadence tiers, keyed by seconds-to-finish (descending).
    'cadence' => [
        1800 => 300,   // > 30 min
        300  => 60,    // 5-30 min
        0    => 20,    // < 5 min
    ],

    'jitter' => 0.20,   // ±20% on every poll delay
];