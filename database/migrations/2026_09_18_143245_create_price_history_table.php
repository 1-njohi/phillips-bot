<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::create('price_history', function (Blueprint $table) {
            $table->id();
            $table->foreignId('vehicle_id')->constrained()->cascadeOnDelete();
            $table->unsignedInteger('price');
            $table->string('state_text')->nullable();
            $table->timestamp('recorded_at')->index();
            $table->index(['vehicle_id', 'recorded_at']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('price_history');
    }
};