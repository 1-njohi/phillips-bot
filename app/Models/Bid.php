<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

class Bid extends Model
{
    public $timestamps = false;

    protected $fillable = [
        'vehicle_id',
        'armed_bid_id',
        'amount',
        'fired_at',
        'response_json',
        'success',
    ];

    protected $casts = [
        'response_json' => 'array',
        'success' => 'boolean',
        'fired_at' => 'datetime',
        'created_at' => 'datetime',
    ];

    public function vehicle(): BelongsTo
    {
        return $this->belongsTo(Vehicle::class);
    }

    public function armedBid(): BelongsTo
    {
        return $this->belongsTo(ArmedBid::class);
    }
}