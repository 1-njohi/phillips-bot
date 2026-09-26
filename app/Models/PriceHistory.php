<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

class PriceHistory extends Model
{
    protected $guarded = [];
    public $timestamps = false;
    protected $fillable = [
        'vehicle_id',
        'price',
        'state_text',
        'source',
        'recorded_at',
    ];
    protected $casts = [
        'recorded_at' => 'datetime',
    ];

    public function vehicle(): BelongsTo
    {
        return $this->belongsTo(Vehicle::class);
    }
}