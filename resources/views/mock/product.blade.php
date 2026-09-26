<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{{ $vehicle->name }} – Mock Auction Page</title>
</head>
<body>
    {{--
        This page mimics the structure of a real Phillips product page
        for the purposes of testing the Python sniper's HTML parser.
        The key attribute is data-finish-time on the #time div.
    --}}

    <h1 class="product_title">{{ $vehicle->name }}</h1>

    <div class="product-meta">
        <span class="wp-product-id">{{ $vehicle->wp_product_id }}</span>
        <span class="slug">{{ $vehicle->slug }}</span>
        <span class="category">{{ is_array($vehicle->categories) ? implode(',', $vehicle->categories) : $vehicle->categories }}</span>
    </div>

    <div id="time" class="timetito"
         data-finish-time="{{ $finishTime }}"
         data-current-time="{{ now()->timestamp }}"
         data-remaining-time="{{ $finishTime - now()->timestamp }}"
         data-bid-increment="1"
         data-currency="KES"
         data-product="{{ $vehicle->wp_product_id }}"
         data-current="{{ number_format($currentPrice, 2, '.', '') }}"
         data-finish="{{ $finishTime }}">
        <div class="yith-wcact-time-left-main">
            <p class="ywcact-time-left">Time left:</p>
            <div class="timer" id="timer_auction"
                 data-product-id="{{ $vehicle->wp_product_id }}"
                 data-current-time="{{ now()->timestamp }}"
                 data-finish-time="{{ $finishTime }}"
                 data-finish="{{ $finishTime }}">
                <span id="days">00</span>
                <span id="hours">00</span>
                <span id="minutes">00</span>
                <span id="seconds">00</span>
            </div>
        </div>
    </div>

    @if ($isOpen)
        <p class="price">Current bid: KSh {{ number_format($currentPrice) }}</p>
    @else
        <p class="price"><span class="ywcact-sealed-auction">This is a blind auction</span></p>
    @endif

    <div class="yith-wcact-currency">
        <input type="hidden" id="yith_wcact_currency" name="yith_wcact_currency" value="KES">
        <input type="hidden" id="yith-wcact-product-id" name="yith-wcact-product" value="{{ $vehicle->wp_product_id }}">
    </div>

    <script>
        // Mimic the real page's JS config object. The Python script may
        // reference these fields if it ever parses JS objects directly.
        var ywcact_frontend_object = {
            ajaxurl: "{{ url('/wp-admin/admin-ajax') }}",
            live_auction_product_page: "0",
            add_bid: "MOCK_NONCE_FROM_DB",
            bid_empty_error: "Please insert a value for your bid.",
            update_list_bids: "MOCK_UPDATE_NONCE",
            small_blocks_background_color: "#ffffff",
            ajax_activated: "1"
        };
        var date_params = {
            format: "j/n/Y h:i:s",
            show_in_customer_time: "1",
            actual_bid_add_value: "5000"
        };
    </script>
</body>
</html>