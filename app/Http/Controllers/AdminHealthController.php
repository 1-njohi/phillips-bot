<?php

namespace App\Http\Controllers;

use App\Services\SystemHealth;
use Illuminate\Http\JsonResponse;
use Inertia\Inertia;
use Inertia\Response;

class AdminHealthController extends Controller
{
    public function __construct(protected SystemHealth $health) {}

    public function index(): Response
    {
        return Inertia::render('AdminHealth');
    }

    public function data(): JsonResponse
    {
        return response()->json($this->health->snapshot());
    }
}