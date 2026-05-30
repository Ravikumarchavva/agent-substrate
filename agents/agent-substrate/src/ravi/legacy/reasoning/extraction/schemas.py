"""Pre-built Pydantic schemas for common extraction use cases.

Each schema is a plain Pydantic ``BaseModel`` subclass.  Pass any schema
to ``Extractor(schema=Invoice)`` to extract structured data from
unstructured text, images, or documents.

These are *starter* schemas — extend or replace them with your own models
for domain-specific needs.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Financial ────────────────────────────────────────────────────────────────


class InvoiceLineItem(BaseModel):
    """A single line item on an invoice."""

    description: str = Field(..., description="Product or service description")
    quantity: float = Field(1.0, description="Quantity ordered")
    unit_price: float = Field(..., description="Price per unit")
    amount: float = Field(..., description="Line total (quantity × unit_price)")


class Invoice(BaseModel):
    """Structured representation of an invoice document."""

    invoice_number: Optional[str] = Field(None, description="Invoice ID/number")
    invoice_date: Optional[date] = Field(None, description="Date of invoice")
    due_date: Optional[date] = Field(None, description="Payment due date")
    vendor_name: str = Field(..., description="Name of the vendor/supplier")
    vendor_address: Optional[str] = Field(None, description="Vendor address")
    customer_name: Optional[str] = Field(None, description="Customer/buyer name")
    customer_address: Optional[str] = Field(None, description="Customer address")
    line_items: List[InvoiceLineItem] = Field(
        default_factory=list, description="Individual line items"
    )
    subtotal: Optional[float] = Field(None, description="Subtotal before tax")
    tax_amount: Optional[float] = Field(None, description="Tax amount")
    tax_rate: Optional[str] = Field(None, description="Tax rate (e.g. '8.25%')")
    total_amount: float = Field(..., description="Total amount due")
    currency: str = Field("USD", description="ISO 4217 currency code")
    payment_terms: Optional[str] = Field(None, description="Payment terms")
    notes: Optional[str] = Field(None, description="Additional notes")


class ReceiptLineItem(BaseModel):
    """A single line item on a receipt."""

    description: str = Field(..., description="Item description")
    quantity: float = Field(1.0, description="Quantity")
    amount: float = Field(..., description="Line total")


class Receipt(BaseModel):
    """Structured representation of a purchase receipt."""

    merchant_name: str = Field(..., description="Store/merchant name")
    merchant_address: Optional[str] = Field(None, description="Store address")
    receipt_date: Optional[date] = Field(None, description="Transaction date")
    receipt_number: Optional[str] = Field(
        None, description="Receipt/transaction number"
    )
    line_items: List[ReceiptLineItem] = Field(
        default_factory=list, description="Purchased items"
    )
    subtotal: Optional[float] = Field(None, description="Subtotal before tax")
    tax_amount: Optional[float] = Field(None, description="Tax amount")
    total_amount: float = Field(..., description="Total paid")
    payment_method: Optional[str] = Field(
        None, description="Payment method (cash, credit card, etc.)"
    )
    currency: str = Field("USD", description="ISO 4217 currency code")


# ── People / HR ──────────────────────────────────────────────────────────────


class BusinessCard(BaseModel):
    """Structured representation of a business card."""

    full_name: str = Field(..., description="Person's full name")
    job_title: Optional[str] = Field(None, description="Job title or role")
    company: Optional[str] = Field(None, description="Company/organization name")
    email: Optional[str] = Field(None, description="Email address")
    phone: Optional[str] = Field(None, description="Phone number")
    website: Optional[str] = Field(None, description="Website URL")
    address: Optional[str] = Field(None, description="Physical address")
    linkedin: Optional[str] = Field(None, description="LinkedIn profile URL")


class WorkExperience(BaseModel):
    """A single work experience entry."""

    company: str = Field(..., description="Company name")
    role: str = Field(..., description="Job title/role")
    start_date: Optional[str] = Field(None, description="Start date")
    end_date: Optional[str] = Field(None, description="End date or 'Present'")
    description: Optional[str] = Field(None, description="Role description")


class Education(BaseModel):
    """A single education entry."""

    institution: str = Field(..., description="School/university name")
    degree: str = Field(..., description="Degree type and field")
    graduation_date: Optional[str] = Field(None, description="Graduation date")


class Resume(BaseModel):
    """Structured representation of a resume/CV."""

    full_name: str = Field(..., description="Candidate's full name")
    email: Optional[str] = Field(None, description="Email address")
    phone: Optional[str] = Field(None, description="Phone number")
    location: Optional[str] = Field(None, description="City/region")
    summary: Optional[str] = Field(None, description="Professional summary")
    skills: List[str] = Field(default_factory=list, description="Key skills")
    experience: List[WorkExperience] = Field(
        default_factory=list, description="Work experience"
    )
    education: List[Education] = Field(
        default_factory=list, description="Education history"
    )
    certifications: List[str] = Field(
        default_factory=list, description="Certifications"
    )
    languages: List[str] = Field(default_factory=list, description="Spoken languages")


# ── Legal ────────────────────────────────────────────────────────────────────


class ContractParty(BaseModel):
    """A party in a contract."""

    name: str = Field(..., description="Party name (person or organization)")
    role: Optional[str] = Field(None, description="Role in contract (e.g. 'Buyer')")
    address: Optional[str] = Field(None, description="Address")


class Contract(BaseModel):
    """Structured representation of a contract document."""

    title: Optional[str] = Field(None, description="Contract title")
    contract_type: Optional[str] = Field(
        None, description="Type of contract (NDA, SLA, MSA, etc.)"
    )
    effective_date: Optional[date] = Field(None, description="Effective date")
    expiration_date: Optional[date] = Field(None, description="Expiration date")
    parties: List[ContractParty] = Field(
        default_factory=list, description="Contracting parties"
    )
    key_terms: List[str] = Field(
        default_factory=list, description="Key terms and conditions"
    )
    obligations: List[str] = Field(default_factory=list, description="Key obligations")
    total_value: Optional[float] = Field(None, description="Contract monetary value")
    currency: Optional[str] = Field(None, description="Currency code")
    governing_law: Optional[str] = Field(None, description="Governing law/jurisdiction")
