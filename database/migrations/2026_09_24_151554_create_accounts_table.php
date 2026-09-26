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
        Schema::create('accounts', function (Blueprint $table) {
            $table->id();
            $table->foreignId('user_id')->constrained()->cascadeOnDelete();
            $table->string('label');
            $table->text('cookie');                    // encrypted cast
            $table->text('nonce');                     // encrypted cast
            $table->timestamp('cookie_captured_at')->nullable();
            $table->enum('status', [
                'unverified',
                'valid',
                'invalid',
                'expired',
            ])->default('unverified');
            $table->boolean('deposit_paid')->default(false);
            $table->timestamps();
        });
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        Schema::dropIfExists('accounts');
    }
};
