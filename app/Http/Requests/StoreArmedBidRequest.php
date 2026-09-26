<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

class StoreArmedBidRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    public function rules(): array
    {
        $increment = (int) config('auction.increment');

        return [
            'account_id' => ['required', 'integer', 'exists:accounts,id'],
            'vehicle_id' => ['required', 'integer', 'exists:vehicles,id'],
            'max_amount' => [
                'required', 'integer',
                'min:' . $increment,
            ],
        ];
    }

    public function messages(): array
    {
        $increment = (int) config('auction.increment');

        return [
            'max_amount.multiple_of' => "Max must be a multiple of {$increment}.",
        ];
    }
}