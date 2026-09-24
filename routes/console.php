<?php

use App\Jobs\FetchRosterJob;
use Illuminate\Support\Facades\Schedule;

Schedule::command('auction:resume')
    ->everyMinute()
    ->withoutOverlapping();

Schedule::job(new FetchRosterJob())
    ->everyFiveMinutes()
    ->withoutOverlapping();