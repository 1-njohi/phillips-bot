<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class UpdateAccountRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    public function rules(): array
    {
        return [
            'label'        => ['sometimes', 'string', 'max:100'],
            'cookie'       => ['sometimes', 'string', 'max:20000'],
            'nonce'        => ['sometimes', 'string', 'max:255'],
            'status'       => ['sometimes', 'in:unverified,valid,invalid,expired'],
            'deposit_paid' => ['sometimes', 'boolean'],
        ];
    }
}