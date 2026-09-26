<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::create('vehicles', function (Blueprint $table) {
            $table->id();
            $table->unsignedBigInteger('wp_product_id')->unique();
            $table->string('name');
            $table->string('slug')->nullable();
            $table->json('categories')->nullable();
            $table->timestamp('finish_time')->nullable()->index();
            $table->unsignedBigInteger('wp_modified_at')->nullable();
            $table->string('state')->default('discovered')->index();
            $table->timestamp('last_polled_at')->nullable();
            $table->integer('increment')->default(5000);
            $table->timestamps();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('vehicles');
    }
};