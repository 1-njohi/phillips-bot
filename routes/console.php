<?php

use App\Jobs\FetchRosterJob;
use Illuminate\Support\Facades\Schedule;
use App\Jobs\TriggerAllSnipersJob;

Schedule::job(new TriggerAllSnipersJob())->everyMinute()->withoutOverlapping();

Schedule::job(new FetchRosterJob())
    ->everyFiveMinutes()
    ->withoutOverlapping();