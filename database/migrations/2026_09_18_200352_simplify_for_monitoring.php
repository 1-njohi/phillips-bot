<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
        public function up(): void
    {
        Schema::dropIfExists('bids');

        Schema::table('vehicles', function (Blueprint $table) {
            if (!Schema::hasColumn('vehicles', 'current_price')) {
                $table->unsignedInteger('current_price')->nullable()->after('finish_time');
            }
            if (!Schema::hasColumn('vehicles', 'last_price_change_at')) {
                $table->timestamp('last_price_change_at')->nullable()->after('current_price');
            }
        });
    }

    public function down(): void
    {
        Schema::table('vehicles', function (Blueprint $table) {
            $table->dropColumn(['current_price', 'last_price_change_at']);
        });
    }
};
