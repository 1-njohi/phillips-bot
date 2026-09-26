<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class StoreAccountRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    public function rules(): array
    {
        return [
            'label'  => ['required', 'string', 'max:100'],
            'cookie' => ['required', 'string', 'max:20000'],
            'nonce'  => ['required', 'string', 'max:255'],
        ];
    }
}