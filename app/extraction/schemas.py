from typing import Literal

from pydantic import BaseModel, Field

FIELD_NAMES = ("supplier", "invoice_date", "invoice_number", "amount", "currency")

Confidence = Literal["high", "medium", "low"]
FieldStatus = Literal["ok", "needs_review"]


class ExtractedField(BaseModel):
    value: str | None = Field(description="The normalised value, or null if the field is not on the document.")
    confidence: Confidence = Field(description="high only when the value is printed clearly and unambiguously.")
    evidence: str = Field(description="The exact text from the document the value was read from. Empty if missing.")
    note: str = Field(description="Why the field is missing or uncertain. Empty when confident.")


class InvoiceExtraction(BaseModel):
    supplier: ExtractedField
    invoice_date: ExtractedField
    invoice_number: ExtractedField
    amount: ExtractedField
    currency: ExtractedField


class FieldResult(BaseModel):
    value: str | None
    candidate: str | None
    confidence: Confidence
    status: FieldStatus
    reasons: list[str]
    evidence: str


class InvoiceResult(BaseModel):
    file: str
    source_path: str
    scanned: bool
    model: str
    fields: dict[str, FieldResult]

    @property
    def needs_review(self):
        return any(field.status == "needs_review" for field in self.fields.values())

    @property
    def review_fields(self):
        return [name for name, field in self.fields.items() if field.status == "needs_review"]

    def value(self, name):
        return self.fields[name].value
