<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;

class Account extends Model
{
    protected $fillable = [
        'user_id',
        'label',
        'cookie',
        'nonce',
        'cookie_captured_at',
        'status',
        'deposit_paid',
    ];

    protected $casts = [
        'cookie' => 'encrypted',
        'nonce' => 'encrypted',
        'cookie_captured_at' => 'datetime',
        'deposit_paid' => 'boolean',
    ];

    // Never expose these over the wire, even accidentally.
    protected $hidden = [
        'cookie',
        'nonce',
    ];

    public function user(): BelongsTo
    {
        return $this->belongsTo(User::class);
    }

    public function armedBids(): HasMany
    {
        return $this->hasMany(ArmedBid::class);
    }

    public function isValid(): bool
    {
        return $this->status === 'valid';
    }

    /**
     * Maximum two vehicles per account. Count only arms that are
     * still live (not ended, won, lost, errored, or priced out).
     */
    public function liveArmCount(): int
    {
        return $this->armedBids()
            ->whereIn('status', ['armed', 'firing'])
            ->count();
    }

    public function canArmAnother(): bool
    {
        return $this->liveArmCount() < 2;
    }
}