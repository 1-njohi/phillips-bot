<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    /**
     * Run the migrations.
     */
    public function up(): void
    {
        Schema::create('armed_bids', function (Blueprint $table) {
            $table->id();
            $table->foreignId('user_id')->constrained()->cascadeOnDelete();
            $table->foreignId('account_id')->constrained()->cascadeOnDelete();
            $table->foreignId('vehicle_id')->constrained()->cascadeOnDelete();
            $table->unsignedBigInteger('max_amount');
            $table->enum('status', [
                'armed',
                'firing',
                'ended',
                'won',
                'lost',
                'priced_out',
                'errored',
                'not_bid',
            ])->default('armed');
            $table->unsignedBigInteger('last_bid')->nullable();
            $table->timestamp('last_bid_at')->nullable();
            $table->unsignedInteger('our_bid_count')->default(0);
            $table->unsignedBigInteger('final_price')->nullable();
            $table->timestamps();

            $table->unique(['account_id', 'vehicle_id']);
            $table->index('vehicle_id');
        });
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        Schema::dropIfExists('armed_bids');
    }
};
