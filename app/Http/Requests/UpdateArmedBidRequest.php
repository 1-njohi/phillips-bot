<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

class UpdateArmedBidRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    public function rules(): array
    {
        $increment = (int) config('auction.increment');

        return [
            'max_amount' => [
                'required', 'integer',
                'min:' . $increment,
                Rule::multipleOf($increment),
            ],
        ];
    }
}