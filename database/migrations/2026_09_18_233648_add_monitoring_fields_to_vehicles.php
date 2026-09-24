<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::table('vehicles', function (Blueprint $table) {
            $table->boolean('watched')->default(false)->after('state')->index();
            $table->unsignedInteger('watch_price')->nullable()->after('watched');
        });
    }

    public function down(): void
    {
        Schema::table('vehicles', function (Blueprint $table) {
            $table->dropColumn(['watched', 'watch_price']);
        });
    }
};