<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;

class ArmedBid extends Model
{
    protected $fillable = [
        'user_id',
        'account_id',
        'vehicle_id',
        'max_amount',
        'status',
        'last_bid',
        'last_bid_at',
        'our_bid_count',
        'final_price',
    ];

    protected $casts = [
        'last_bid_at' => 'datetime',
    ];

    public function user(): BelongsTo
    {
        return $this->belongsTo(User::class);
    }

    public function account(): BelongsTo
    {
        return $this->belongsTo(Account::class);
    }

    public function vehicle(): BelongsTo
    {
        return $this->belongsTo(Vehicle::class);
    }

    public function bids(): HasMany
    {
        return $this->hasMany(Bid::class);
    }

    /**
     * Live status for the UI, derived from the vehicle's last-seen
     * price and this arm's last bid.
     *
     *   unknown       — no price yet
     *   priced_out    — current price at or above max
     *   not_bidding   — no bid fired yet
     *   winning       — our last bid equals the current price
     *   outbid        — current price is strictly higher than ours
     */
    public function liveStatus(): string
    {
        $price = $this->vehicle?->current_price;
        $last = $this->last_bid;

        if ($price === null) {
            return 'unknown';
        }

        if ($price >= $this->max_amount) {
            return 'priced_out';
        }

        if ($last === null) {
            return 'not_bidding';
        }

        if ($last === $price) {
            return 'winning';
        }

        return 'outbid';
    }

    public function isLive(): bool
    {
        return in_array($this->status, ['armed', 'firing'], true);
    }
}