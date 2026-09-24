<?php

namespace App\Services;

use Illuminate\Http\Client\PendingRequest;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;

class AuctionClient
{
    public function __construct(protected string $baseUrl)
    {
    }

    public static function fromConfig(): self
    {
        return new self(rtrim(config('auction.wp_url'), '/'));
    }

    protected function client(): PendingRequest
    {
        return Http::baseUrl($this->baseUrl)
            ->acceptJson()
            ->timeout(10)
            ->retry(2, 500, throw: false);
    }

    /**
     * GET /wp-json/wp/v2/product?per_page=100
     *
     * @return array<int, array{
     *     id:int, name:string, slug:?string, categories:array,
     *     finish_time:?int, bid_count:int, modified:?int
     * }>
     */
    public function roster(int $perPage = 100): array
    {
        $res = $this->client()->get('/wp-json/wp/v2/product', [
            'per_page' => $perPage,
            '_fields' => 'id,slug,title,modified,product_cat,auction',
        ]);

        AuctionClock::observe($res->header('Date'));

        if (!$res->successful())
            return [];

        return collect($res->json())->map(fn($p) => [
            'id' => (int) $p['id'],
            'name' => $p['title']['rendered'] ?? '',
            'slug' => $p['slug'] ?? null,
            'categories' => $p['product_cat'] ?? [],
            'finish_time' => isset($p['auction']['finish_time'])
                ? (int) $p['auction']['finish_time']
                : null,
            'bid_count' => (int) ($p['auction']['bid_count'] ?? 0),
            'modified' => isset($p['modified'])
                ? strtotime($p['modified'] . ' UTC')
                : null,
        ])->all();
    }

    /**
     * GET /wp-json/wc/store/v1/products/{id}
     *
     * @return array{
     *     id:int, current_price:int, status:string,
     *     raw_price:string, button_text:?string, server_time:int
     * }|null
     */
    public function state(int $productId, ?int $finishTime = null): ?array
    {
        $res = $this->client()->get("/wp-json/wc/store/v1/products/{$productId}");

        AuctionClock::observe($res->header('Date'));

        if (!$res->successful())
            return null;
        $p = $res->json();
        $raw = $p['prices']['price'] ?? 0;
        $button = $p['add_to_cart']['text'] ?? null;

        $price = is_string($raw)
            ? (int) filter_var($raw, FILTER_SANITIZE_NUMBER_INT)
            : (int) $raw;

        if ($finishTime !== null) {
            $status = $finishTime > time() ? 'open' : 'closed';
        } else {
            $status = $button && stripos($button, 'bid') !== false ? 'open' : 'closed';
        }

        return [
            'id' => $productId,
            'current_price' => $price,
            'status' => $status,
            'raw_price' => (string) $raw,
            'button_text' => $button,
            'server_time' => strtotime($res->header('Date') ?: 'now'),
        ];
    }

}