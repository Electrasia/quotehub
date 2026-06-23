"""
backend/routes/suppliers.py — Supplier management API.

This module provides CRUD endpoints for suppliers, contacts, aliases,
capabilities, brands, and product types.  All endpoints require
authentication and enforce role-based access via ``require_role()``.

Roles:
    - **user+**:     read-only endpoints (list, detail, resolve, autocomplete)
    - **admin+**:    create, update, delete (with constraints on ``verified`` and ``status``)
    - **master**:    full access (inactivate, set verified, any status)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_role
from ..db import get_db
from ..suppliers import normalize_name, resolve_supplier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/suppliers", tags=["suppliers"])

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _make_audit_entry(action: str, user: dict, details: dict | None = None) -> dict:
    """Build a supplier_audit_log row dict."""
    entry = {
        "action": action,
        "actor": user.get("username", "unknown"),
    }
    if details:
        entry["details"] = json.dumps(details, ensure_ascii=False)
    else:
        entry["details"] = "{}"
    return entry


def _write_audit_log(db, supplier_id: int, entry: dict):
    """Insert an audit log row for a supplier."""
    db.execute(
        "INSERT INTO supplier_audit_log (supplier_id, action, actor, details) "
        "VALUES (?, ?, ?, ?)",
        (supplier_id, entry["action"], entry["actor"], entry["details"]),
    )


def _get_supplier_or_404(db, supplier_id: int) -> dict:
    """Fetch a supplier by id, raising 404 if not found."""
    row = db.execute(
        "SELECT * FROM suppliers WHERE id = ?", (supplier_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return dict(row)


def _can_set_status(user: dict, desired: str) -> bool:
    """Check if a user is allowed to set a given supplier status."""
    role = user.get("role", "")
    if role == "master":
        return desired in ("active", "inactive", "review")
    # admin: only active or inactive
    if desired == "review":
        return False
    return desired in ("active", "inactive")


def _diff_dicts(before: dict, after: dict, *keys: str) -> dict:
    """Return a dict containing only the keys that changed between before and after."""
    diff = {}
    for k in keys:
        vb = before.get(k)
        va = after.get(k)
        if vb != va:
            diff[k] = {"from": vb, "to": va}
    return diff


def _get_brand_or_create(db, name: str) -> int:
    """Resolve a brand name to its ID, creating it if necessary.

    This function is used within supplier capability writes.
    Brand names are normalised via :func:`~backend.suppliers.normalize_name`.
    """
    norm = normalize_name(name)
    if not norm:
        raise HTTPException(status_code=422, detail="Brand name cannot be empty after normalisation")

    db.execute("INSERT OR IGNORE INTO brands (name) VALUES (?)", (norm,))
    row = db.execute("SELECT id FROM brands WHERE name = ?", (norm,)).fetchone()
    if not row:
        # Race condition guard — retry once
        db.execute("INSERT OR IGNORE INTO brands (name) VALUES (?)", (norm,))
        row = db.execute("SELECT id FROM brands WHERE name = ?", (norm,)).fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Failed to create brand")
    return row["id"]


def _get_product_type_or_create(db, name: str) -> int:
    """Resolve a product type name to its ID, creating it if necessary.

    Same semantics as :func:`_get_brand_or_create`.
    """
    norm = normalize_name(name)
    if not norm:
        raise HTTPException(status_code=422, detail="Product type name cannot be empty after normalisation")

    db.execute("INSERT OR IGNORE INTO product_types (name) VALUES (?)", (norm,))
    row = db.execute("SELECT id FROM product_types WHERE name = ?", (norm,)).fetchone()
    if not row:
        db.execute("INSERT OR IGNORE INTO product_types (name) VALUES (?)", (norm,))
        row = db.execute("SELECT id FROM product_types WHERE name = ?", (norm,)).fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Failed to create product_type")
    return row["id"]


# ─── Pydantic models ─────────────────────────────────────────────────────────

class SupplierCreate(BaseModel):
    canonical_name: str
    display_name: str = ""
    notes: str = ""


class SupplierUpdate(BaseModel):
    display_name: Optional[str] = None
    status: Optional[str] = Field(default=None, pattern="^(active|inactive|review)$")
    notes: Optional[str] = None


class ContactCreate(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    role: str = ""
    position: int = 0
    is_default_rfq_contact: int = 0


class ContactUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    position: Optional[int] = None
    is_default_rfq_contact: Optional[int] = None


class AliasCreate(BaseModel):
    alias: str


class CapabilityCreate(BaseModel):
    brand: str
    product_type: str
    verified: bool = False


class CapabilityUpdate(BaseModel):
    brand: Optional[str] = None
    product_type: Optional[str] = None
    verified: Optional[bool] = None


class ResolveRequest(BaseModel):
    name: str


class BrandCreate(BaseModel):
    name: str


class ProductTypeCreate(BaseModel):
    name: str


# =============================================================================
# SUPPLIERS
# =============================================================================

@router.get("", dependencies=[Depends(require_role("user", "admin", "master"))])
async def list_suppliers(
    q: str = "",
    status: str = "active",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
):
    """List suppliers with optional search, status filter, and pagination."""
    with get_db(readonly=True) as db:
        conditions = []
        params: list = []

        if status and status != "all":
            conditions.append("s.status = ?")
            params.append(status)

        if q:
            conditions.append("s.canonical_name LIKE ?")
            params.append(f"%{normalize_name(q)}%")

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        # Count
        count_row = db.execute(
            f"SELECT COUNT(*) AS total FROM suppliers s {where}", params
        ).fetchone()
        total = count_row["total"] if count_row else 0

        # Items
        offset = (page - 1) * per_page
        rows = db.execute(
            f"SELECT s.* FROM suppliers s {where} ORDER BY s.canonical_name LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

        items = [dict(r) for r in rows]

    return {"items": items, "total": total, "page": page, "per_page": per_page}


@router.post("", dependencies=[Depends(require_role("admin", "master"))])
async def create_supplier(body: SupplierCreate, user: dict = Depends(require_role("admin", "master"))):
    """Create a new supplier.

    ``canonical_name`` is normalised via :func:`~backend.suppliers.normalize_name`.
    Duplicates return 409.
    New suppliers always get ``status='active'``.
    """
    norm = normalize_name(body.canonical_name)
    if not norm:
        raise HTTPException(status_code=422, detail="canonical_name must not be empty after normalisation")

    display = body.display_name.strip() if body.display_name else norm
    notes = body.notes.strip() if body.notes else ""

    with get_db() as db:
        try:
            cur = db.execute(
                "INSERT INTO suppliers (canonical_name, display_name, status, notes) "
                "VALUES (?, ?, 'active', ?)",
                (norm, display, notes),
            )
            supplier_id = cur.lastrowid
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HTTPException(status_code=409, detail="Supplier with this canonical_name already exists")
            raise

        _write_audit_log(db, supplier_id, _make_audit_entry(
            "create_supplier", user,
            {"canonical_name": norm, "display_name": display},
        ))

        supplier = dict(db.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone())

    return supplier


@router.get("/{supplier_id}", dependencies=[Depends(require_role("user", "admin", "master"))])
async def get_supplier(supplier_id: int):
    """Get a supplier with nested aliases, contacts, and capabilities."""
    with get_db(readonly=True) as db:
        supplier = _get_supplier_or_404(db, supplier_id)

        # Fetch aliases
        aliases = [
            dict(r) for r in db.execute(
                "SELECT id, alias, created_at FROM supplier_aliases WHERE supplier_id = ? ORDER BY id",
                (supplier_id,),
            ).fetchall()
        ]

        # Fetch contacts
        contacts = [
            dict(r) for r in db.execute(
                "SELECT * FROM contacts WHERE supplier_id = ? ORDER BY position ASC, id ASC",
                (supplier_id,),
            ).fetchall()
        ]

        # Fetch capabilities (with brand and product_type names)
        capabilities = [
            {
                "id": r["id"],
                "brand": bname,
                "product_type": ptname,
                "verified": r["verified"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r, bname, ptname in db.execute(
                """SELECT c.*, b.name AS brand_name, pt.name AS product_type_name
                   FROM supplier_capabilities c
                   JOIN brands b ON b.id = c.brand_id
                   JOIN product_types pt ON pt.id = c.product_type_id
                   WHERE c.supplier_id = ?
                   ORDER BY c.id""",
                (supplier_id,),
            ).fetchall()
        ]

    supplier["aliases"] = aliases
    supplier["contacts"] = contacts
    supplier["capabilities"] = capabilities
    return supplier


@router.put("/{supplier_id}", dependencies=[Depends(require_role("admin", "master"))])
async def update_supplier(
    supplier_id: int,
    body: SupplierUpdate,
    user: dict = Depends(require_role("admin", "master")),
):
    """Update a supplier's ``display_name``, ``status``, or ``notes``.

    Admin may only set status to ``active`` or ``inactive``.
    Admin attempting ``review`` → 403.
    Master may set any status.
    """
    with get_db() as db:
        supplier = _get_supplier_or_404(db, supplier_id)

        # Build update
        updates = {}
        if body.display_name is not None:
            updates["display_name"] = body.display_name.strip()
        if body.status is not None:
            if not _can_set_status(user, body.status):
                raise HTTPException(
                    status_code=403,
                    detail="Only master can set status to 'review'. Admin may set 'active' or 'inactive'.",
                )
            updates["status"] = body.status
        if body.notes is not None:
            updates["notes"] = body.notes.strip()

        if not updates:
            return supplier  # nothing to change

        # Compute diff for audit log (only changed fields)
        diff = _diff_dicts(supplier, updates, *updates.keys())

        # Apply update
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        db.execute(
            f"UPDATE suppliers SET {set_clause}, updated_at = ? WHERE id = ?",
            list(updates.values()) + [_now_iso(), supplier_id],
        )

        _write_audit_log(db, supplier_id, _make_audit_entry(
            "update_supplier", user, diff if diff else {"no_changes": True},
        ))

        updated = dict(db.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone())

    return updated


@router.post("/{supplier_id}/inactivate", dependencies=[Depends(require_role("master"))])
async def inactivate_supplier(
    supplier_id: int,
    user: dict = Depends(require_role("master")),
):
    """Soft-delete a supplier by setting ``status`` to ``inactive``.

    Master-only.  No DELETE endpoint exists on ``/suppliers/{id}``.
    """
    with get_db() as db:
        supplier = _get_supplier_or_404(db, supplier_id)

        before = supplier["status"]
        if before == "inactive":
            return supplier  # already inactive, no-op

        db.execute(
            "UPDATE suppliers SET status = 'inactive', updated_at = ? WHERE id = ?",
            (_now_iso(), supplier_id),
        )

        _write_audit_log(db, supplier_id, _make_audit_entry(
            "inactivate_supplier", user,
            {"before_status": before, "after_status": "inactive"},
        ))

        updated = dict(db.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone())

    return updated


@router.delete("/{supplier_id}/purge", dependencies=[Depends(require_role("master"))])
async def purge_supplier(
    supplier_id: int,
    user: dict = Depends(require_role("master")),
):
    """Permanently delete a supplier and all its data.

    Master-only.  Stores a complete snapshot in the audit log **before**
    deletion.  Preconditions:
      - Supplier must exist.
      - Status must be ``active`` or ``review`` (not ``inactive``).
      - Supplier must be **≤ 24 hours old** OR have **zero** quotations,
        contacts, aliases, and capabilities.

    Returns 409 with a descriptive message when preconditions fail.
    """
    with get_db() as db:
        supplier = _get_supplier_or_404(db, supplier_id)

        # ── Precondition 1: status ──────────────────────────────────────
        status = supplier["status"]
        if status == "inactive":
            raise HTTPException(
                status_code=409,
                detail="Cannot purge: supplier status is 'inactive'. Already removed from active use.",
            )

        # ── Precondition 2: age check ───────────────────────────────────
        created = supplier.get("created_at", "")
        is_new = False
        if created:
            try:
                created_dt = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
                now = datetime.now(timezone.utc)
                age = now - created_dt.replace(tzinfo=timezone.utc)
                is_new = age.total_seconds() <= 86400  # 24 hours
            except ValueError:
                is_new = False

        # ── Precondition 3: reference counts ────────────────────────────
        quotation_count = db.execute(
            "SELECT COUNT(*) AS n FROM quotations WHERE supplier_id = ?",
            (supplier_id,),
        ).fetchone()["n"]

        contact_count = db.execute(
            "SELECT COUNT(*) AS n FROM contacts WHERE supplier_id = ?",
            (supplier_id,),
        ).fetchone()["n"]

        alias_count = db.execute(
            "SELECT COUNT(*) AS n FROM supplier_aliases WHERE supplier_id = ?",
            (supplier_id,),
        ).fetchone()["n"]

        capability_count = db.execute(
            "SELECT COUNT(*) AS n FROM supplier_capabilities WHERE supplier_id = ?",
            (supplier_id,),
        ).fetchone()["n"]

        has_any_ref = (quotation_count + contact_count + alias_count + capability_count) > 0

        # ── Block if not new AND has references ─────────────────────────
        if not is_new and has_any_ref:
            if quotation_count > 0:
                plural = "s" if quotation_count > 1 else ""
                detail = (
                    f"Cannot purge: supplier has {quotation_count} linked "
                    f"quotation{plural}. Inactivate instead."
                )
            else:
                parts = []
                if contact_count:
                    parts.append(f"{contact_count} contact{'s' if contact_count > 1 else ''}")
                if alias_count:
                    parts.append(f"{alias_count} aliase{'s' if alias_count > 1 else ''}")
                if capability_count:
                    parts.append(f"{capability_count} capabilit{'ies' if capability_count > 1 else 'y'}")
                detail = (
                    "Cannot purge: supplier is older than 24 hours and has "
                    f"{', '.join(parts)}. Inactivate instead."
                )
            raise HTTPException(status_code=409, detail=detail)

        # ── Build snapshot BEFORE deletion ──────────────────────────────
        contacts = [
            dict(r) for r in db.execute(
                "SELECT * FROM contacts WHERE supplier_id = ?", (supplier_id,),
            ).fetchall()
        ]
        aliases = [
            dict(r) for r in db.execute(
                "SELECT * FROM supplier_aliases WHERE supplier_id = ?", (supplier_id,),
            ).fetchall()
        ]
        capabilities = [
            dict(r) for r in db.execute(
                "SELECT * FROM supplier_capabilities WHERE supplier_id = ?", (supplier_id,),
            ).fetchall()
        ]

        snapshot = {
            "supplier": dict(supplier),
            "contacts": contacts,
            "aliases": aliases,
            "capabilities": capabilities,
            "purged_at": _now_iso(),
            "reason": "master_purge",
        }

        # ── Audit log entry BEFORE row deletion ─────────────────────────
        _write_audit_log(db, supplier_id, _make_audit_entry(
            "purge_supplier", user, snapshot,
        ))

        # ── Delete children manually (CASCADE is inactive without
        #    PRAGMA foreign_keys) ────────────────────────────────────────
        db.execute("DELETE FROM contacts WHERE supplier_id = ?", (supplier_id,))
        db.execute("DELETE FROM supplier_aliases WHERE supplier_id = ?", (supplier_id,))
        db.execute("DELETE FROM supplier_capabilities WHERE supplier_id = ?", (supplier_id,))

        # ── Delete the supplier itself ──────────────────────────────────
        db.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))

    return {"detail": "Supplier purged. Snapshot retained in audit log."}


# =============================================================================
# CONTACTS
# =============================================================================

@router.get("/{supplier_id}/contacts", dependencies=[Depends(require_role("user", "admin", "master"))])
async def list_contacts(supplier_id: int):
    """List contacts for a supplier sorted by ``position ASC, id ASC``."""
    with get_db(readonly=True) as db:
        _get_supplier_or_404(db, supplier_id)
        contacts = [
            dict(r) for r in db.execute(
                "SELECT * FROM contacts WHERE supplier_id = ? ORDER BY position ASC, id ASC",
                (supplier_id,),
            ).fetchall()
        ]
    return {"items": contacts}


@router.post("/{supplier_id}/contacts", dependencies=[Depends(require_role("admin", "master"))])
async def create_contact(
    supplier_id: int,
    body: ContactCreate,
    user: dict = Depends(require_role("admin", "master")),
):
    """Add a contact to a supplier."""
    with get_db() as db:
        _get_supplier_or_404(db, supplier_id)

        cur = db.execute(
            "INSERT INTO contacts (supplier_id, name, email, phone, role, position, is_default_rfq_contact) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (supplier_id, body.name, body.email, body.phone, body.role, body.position, body.is_default_rfq_contact),
        )
        contact_id = cur.lastrowid

        _write_audit_log(db, supplier_id, _make_audit_entry(
            "create_contact", user,
            {"contact_id": contact_id, "name": body.name, "email": body.email},
        ))

        contact = dict(db.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone())

    return contact


@router.put("/{supplier_id}/contacts/{contact_id}", dependencies=[Depends(require_role("admin", "master"))])
async def update_contact(
    supplier_id: int,
    contact_id: int,
    body: ContactUpdate,
    user: dict = Depends(require_role("admin", "master")),
):
    """Update a contact."""
    with get_db() as db:
        _get_supplier_or_404(db, supplier_id)

        row = db.execute(
            "SELECT * FROM contacts WHERE id = ? AND supplier_id = ?",
            (contact_id, supplier_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Contact not found")
        contact = dict(row)

        # Build updates
        updates = {}
        for field in ("name", "email", "phone", "role", "position", "is_default_rfq_contact"):
            val = getattr(body, field, None)
            if val is not None:
                updates[field] = val

        if not updates:
            return contact

        diff = _diff_dicts(contact, updates, *updates.keys())

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        db.execute(
            f"UPDATE contacts SET {set_clause}, updated_at = ? WHERE id = ?",
            list(updates.values()) + [_now_iso(), contact_id],
        )

        _write_audit_log(db, supplier_id, _make_audit_entry(
            "update_contact", user, diff if diff else {"no_changes": True},
        ))

        updated = dict(db.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone())

    return updated


@router.delete("/{supplier_id}/contacts/{contact_id}", dependencies=[Depends(require_role("admin", "master"))])
async def delete_contact(
    supplier_id: int,
    contact_id: int,
    user: dict = Depends(require_role("admin", "master")),
):
    """Hard-delete a contact."""
    with get_db() as db:
        _get_supplier_or_404(db, supplier_id)

        row = db.execute(
            "SELECT id, name FROM contacts WHERE id = ? AND supplier_id = ?",
            (contact_id, supplier_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Contact not found")

        db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))

        _write_audit_log(db, supplier_id, _make_audit_entry(
            "delete_contact", user,
            {"contact_id": contact_id, "name": row["name"]},
        ))

    return {"status": "deleted"}


# =============================================================================
# ALIASES
# =============================================================================

@router.get("/{supplier_id}/aliases", dependencies=[Depends(require_role("user", "admin", "master"))])
async def list_aliases(supplier_id: int):
    """List aliases for a supplier."""
    with get_db(readonly=True) as db:
        _get_supplier_or_404(db, supplier_id)
        aliases = [
            dict(r) for r in db.execute(
                "SELECT id, alias, created_at FROM supplier_aliases WHERE supplier_id = ? ORDER BY id",
                (supplier_id,),
            ).fetchall()
        ]
    return {"items": aliases}


@router.post("/{supplier_id}/aliases", dependencies=[Depends(require_role("admin", "master"))])
async def create_alias(
    supplier_id: int,
    body: AliasCreate,
    user: dict = Depends(require_role("admin", "master")),
):
    """Add an alias to a supplier.

    The alias is normalised via :func:`~backend.suppliers.normalize_name`.
    Duplicates return 409.
    """
    norm = normalize_name(body.alias)
    if not norm:
        raise HTTPException(status_code=422, detail="Alias cannot be empty after normalisation")

    with get_db() as db:
        _get_supplier_or_404(db, supplier_id)

        try:
            cur = db.execute(
                "INSERT INTO supplier_aliases (alias, supplier_id) VALUES (?, ?)",
                (norm, supplier_id),
            )
            alias_id = cur.lastrowid
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HTTPException(status_code=409, detail="Alias already exists")
            raise

        _write_audit_log(db, supplier_id, _make_audit_entry(
            "add_alias", user,
            {"alias_id": alias_id, "alias": norm},
        ))

        row = dict(db.execute("SELECT id, alias, created_at FROM supplier_aliases WHERE id = ?", (alias_id,)).fetchone())

    return row


@router.delete("/{supplier_id}/aliases/{alias_id}", dependencies=[Depends(require_role("admin", "master"))])
async def delete_alias(
    supplier_id: int,
    alias_id: int,
    user: dict = Depends(require_role("admin", "master")),
):
    """Hard-delete an alias."""
    with get_db() as db:
        _get_supplier_or_404(db, supplier_id)

        row = db.execute(
            "SELECT id, alias FROM supplier_aliases WHERE id = ? AND supplier_id = ?",
            (alias_id, supplier_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Alias not found")

        db.execute("DELETE FROM supplier_aliases WHERE id = ?", (alias_id,))

        _write_audit_log(db, supplier_id, _make_audit_entry(
            "remove_alias", user,
            {"alias_id": alias_id, "alias": row["alias"]},
        ))

    return {"status": "deleted"}


# =============================================================================
# CAPABILITIES
# =============================================================================

@router.get("/{supplier_id}/capabilities", dependencies=[Depends(require_role("user", "admin", "master"))])
async def list_capabilities(supplier_id: int):
    """List capabilities for a supplier, including brand and product_type names."""
    with get_db(readonly=True) as db:
        _get_supplier_or_404(db, supplier_id)
        capabilities = [
            {
                "id": r["id"],
                "brand": r["brand_name"],
                "product_type": r["product_type_name"],
                "verified": bool(r["verified"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in db.execute(
                """SELECT c.id, b.name AS brand_name, pt.name AS product_type_name,
                          c.verified, c.created_at, c.updated_at
                   FROM supplier_capabilities c
                   JOIN brands b ON b.id = c.brand_id
                   JOIN product_types pt ON pt.id = c.product_type_id
                   WHERE c.supplier_id = ?
                   ORDER BY c.id""",
                (supplier_id,),
            ).fetchall()
        ]
    return {"items": capabilities}


@router.post("/{supplier_id}/capabilities", dependencies=[Depends(require_role("admin", "master"))])
async def create_capability(
    supplier_id: int,
    body: CapabilityCreate,
    user: dict = Depends(require_role("admin", "master")),
):
    """Add a capability to a supplier.

    Admin may not set ``verified=true`` → 403.
    Master may set ``verified`` freely.
    Duplicate ``(supplier_id, brand_id, product_type_id)`` → 409.
    """
    role = user.get("role", "")
    if role != "master" and body.verified:
        raise HTTPException(
            status_code=403,
            detail="Only master can set verified=true on a capability",
        )

    with get_db() as db:
        _get_supplier_or_404(db, supplier_id)

        brand_id = _get_brand_or_create(db, body.brand)
        product_type_id = _get_product_type_or_create(db, body.product_type)

        try:
            cur = db.execute(
                "INSERT INTO supplier_capabilities (supplier_id, brand_id, product_type_id, verified) "
                "VALUES (?, ?, ?, ?)",
                (supplier_id, brand_id, product_type_id, 1 if body.verified else 0),
            )
            cap_id = cur.lastrowid
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HTTPException(
                    status_code=409,
                    detail="This supplier already has this brand + product_type combination",
                )
            raise

        _write_audit_log(db, supplier_id, _make_audit_entry(
            "create_capability", user,
            {"capability_id": cap_id, "brand": body.brand, "product_type": body.product_type, "verified": body.verified},
        ))

        row = db.execute(
            """SELECT c.id, b.name AS brand_name, pt.name AS product_type_name,
                      c.verified, c.created_at, c.updated_at
               FROM supplier_capabilities c
               JOIN brands b ON b.id = c.brand_id
               JOIN product_types pt ON pt.id = c.product_type_id
               WHERE c.id = ?""",
            (cap_id,),
        ).fetchone()

    return {
        "id": row["id"],
        "brand": row["brand_name"],
        "product_type": row["product_type_name"],
        "verified": bool(row["verified"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.put("/{supplier_id}/capabilities/{capability_id}", dependencies=[Depends(require_role("admin", "master"))])
async def update_capability(
    supplier_id: int,
    capability_id: int,
    body: CapabilityUpdate,
    user: dict = Depends(require_role("admin", "master")),
):
    """Update a capability.

    Admin may not modify ``verified`` → 403 if attempted.
    Master may modify ``verified`` freely.
    """
    with get_db() as db:
        _get_supplier_or_404(db, supplier_id)

        row = db.execute(
            "SELECT * FROM supplier_capabilities WHERE id = ? AND supplier_id = ?",
            (capability_id, supplier_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Capability not found")
        cap = dict(row)

        # Resolve current brand/product_type names
        brand_row = db.execute("SELECT name FROM brands WHERE id = ?", (cap["brand_id"],)).fetchone()
        pt_row = db.execute("SELECT name FROM product_types WHERE id = ?", (cap["product_type_id"],)).fetchone()

        updates = {}
        diff_base = {
            "brand": brand_row["name"] if brand_row else str(cap["brand_id"]),
            "product_type": pt_row["name"] if pt_row else str(cap["product_type_id"]),
            "verified": bool(cap["verified"]),
        }

        if body.brand is not None:
            brand_id = _get_brand_or_create(db, body.brand)
            updates["brand_id"] = brand_id
        if body.product_type is not None:
            pt_id = _get_product_type_or_create(db, body.product_type)
            updates["product_type_id"] = pt_id
        if body.verified is not None:
            role = user.get("role", "")
            if role != "master":
                raise HTTPException(
                    status_code=403,
                    detail="Only master can change the verified flag on a capability",
                )
            updates["verified"] = 1 if body.verified else 0

        if not updates:
            # Return current state
            return {
                "id": cap["id"],
                "brand": diff_base["brand"],
                "product_type": diff_base["product_type"],
                "verified": diff_base["verified"],
                "created_at": cap["created_at"],
                "updated_at": cap["updated_at"],
            }

        # Check unique constraint if brand or product_type changed
        if "brand_id" in updates or "product_type_id" in updates:
            new_brand_id = updates.get("brand_id", cap["brand_id"])
            new_pt_id = updates.get("product_type_id", cap["product_type_id"])
            existing = db.execute(
                "SELECT id FROM supplier_capabilities "
                "WHERE supplier_id = ? AND brand_id = ? AND product_type_id = ? AND id != ?",
                (supplier_id, new_brand_id, new_pt_id, capability_id),
            ).fetchone()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail="This supplier already has this brand + product_type combination",
                )

        # Build diff (resolve names for changed brand/product_type)
        new_brand_name = body.brand if body.brand is not None else diff_base["brand"]
        new_pt_name = body.product_type if body.product_type is not None else diff_base["product_type"]
        new_verified = body.verified if body.verified is not None else diff_base["verified"]

        diff = _diff_dicts(
            diff_base,
            {"brand": new_brand_name, "product_type": new_pt_name, "verified": new_verified},
            "brand", "product_type", "verified",
        )

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values())
        db.execute(
            f"UPDATE supplier_capabilities SET {set_clause}, updated_at = ? WHERE id = ?",
            values + [_now_iso(), capability_id],
        )

        _write_audit_log(db, supplier_id, _make_audit_entry(
            "update_capability", user, diff if diff else {"no_changes": True},
        ))

        # Read back
        updated = db.execute(
            """SELECT c.id, b.name AS brand_name, pt.name AS product_type_name,
                      c.verified, c.created_at, c.updated_at
               FROM supplier_capabilities c
               JOIN brands b ON b.id = c.brand_id
               JOIN product_types pt ON pt.id = c.product_type_id
               WHERE c.id = ?""",
            (capability_id,),
        ).fetchone()

    return {
        "id": updated["id"],
        "brand": updated["brand_name"],
        "product_type": updated["product_type_name"],
        "verified": bool(updated["verified"]),
        "created_at": updated["created_at"],
        "updated_at": updated["updated_at"],
    }


@router.delete("/{supplier_id}/capabilities/{capability_id}", dependencies=[Depends(require_role("admin", "master"))])
async def delete_capability(
    supplier_id: int,
    capability_id: int,
    user: dict = Depends(require_role("admin", "master")),
):
    """Hard-delete a capability."""
    with get_db() as db:
        _get_supplier_or_404(db, supplier_id)

        row = db.execute(
            "SELECT id, brand_id, product_type_id FROM supplier_capabilities WHERE id = ? AND supplier_id = ?",
            (capability_id, supplier_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Capability not found")

        db.execute("DELETE FROM supplier_capabilities WHERE id = ?", (capability_id,))

        _write_audit_log(db, supplier_id, _make_audit_entry(
            "delete_capability", user,
            {"capability_id": capability_id},
        ))

    return {"status": "deleted"}


# =============================================================================
# RESOLVE (strictly read-only)
# =============================================================================

@router.post("/resolve", dependencies=[Depends(require_role("user", "admin", "master"))])
async def resolve_supplier_endpoint(body: ResolveRequest):
    """Resolve a supplier name to a supplier ID.

    **Strictly read-only.**  Never creates or modifies records.

    Returns:
        - ``id``: matched supplier ID (``null`` if no match)
        - ``matched``: ``true`` if a match was found
        - ``canonical_name``: the canonical name of the matched supplier (if matched)
    """
    with get_db(readonly=True) as db:
        sid = resolve_supplier(db, body.name)
        if sid is not None:
            row = db.execute(
                "SELECT canonical_name FROM suppliers WHERE id = ?", (sid,)
            ).fetchone()
            return {
                "id": sid,
                "matched": True,
                "canonical_name": row["canonical_name"] if row else None,
            }
        return {"id": None, "matched": False, "canonical_name": None}


# =============================================================================
# BRANDS
# =============================================================================

brands_router = APIRouter(prefix="/brands", tags=["brands"])


@brands_router.get("", dependencies=[Depends(require_role("user", "admin", "master"))])
async def list_brands(q: str = Query("", min_length=0)):
    """Autocomplete brands.

    Requires minimum 2 characters.  Returns max 20 results.
    Indexed prefix match on ``brands.name``.
    """
    if len(q) < 2:
        return {"items": []}
    norm = normalize_name(q)
    if not norm:
        return {"items": []}
    with get_db(readonly=True) as db:
        rows = db.execute(
            "SELECT id, name FROM brands WHERE name LIKE ? ORDER BY name LIMIT 20",
            (f"{norm}%",),
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@brands_router.post("", dependencies=[Depends(require_role("admin", "master"))])
async def create_brand(body: BrandCreate):
    """Create a new brand.

    Name is normalised.  Duplicates return 409.
    """
    norm = normalize_name(body.name)
    if not norm:
        raise HTTPException(status_code=422, detail="Brand name cannot be empty after normalisation")
    with get_db() as db:
        try:
            cur = db.execute("INSERT INTO brands (name) VALUES (?)", (norm,))
            brand_id = cur.lastrowid
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HTTPException(status_code=409, detail="Brand already exists")
            raise
        row = dict(db.execute("SELECT id, name FROM brands WHERE id = ?", (brand_id,)).fetchone())
    return row


# =============================================================================
# PRODUCT TYPES
# =============================================================================

product_types_router = APIRouter(prefix="/product-types", tags=["product-types"])


@product_types_router.get("", dependencies=[Depends(require_role("user", "admin", "master"))])
async def list_product_types(q: str = Query("", min_length=0)):
    """Autocomplete product types.

    Requires minimum 2 characters.  Returns max 20 results.
    Indexed prefix match on ``product_types.name``.
    """
    if len(q) < 2:
        return {"items": []}
    norm = normalize_name(q)
    if not norm:
        return {"items": []}
    with get_db(readonly=True) as db:
        rows = db.execute(
            "SELECT id, name FROM product_types WHERE name LIKE ? ORDER BY name LIMIT 20",
            (f"{norm}%",),
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@product_types_router.post("", dependencies=[Depends(require_role("admin", "master"))])
async def create_product_type(body: ProductTypeCreate):
    """Create a new product type.

    Name is normalised.  Duplicates return 409.
    """
    norm = normalize_name(body.name)
    if not norm:
        raise HTTPException(status_code=422, detail="Product type name cannot be empty after normalisation")
    with get_db() as db:
        try:
            cur = db.execute("INSERT INTO product_types (name) VALUES (?)", (norm,))
            pt_id = cur.lastrowid
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HTTPException(status_code=409, detail="Product type already exists")
            raise
        row = dict(db.execute("SELECT id, name FROM product_types WHERE id = ?", (pt_id,)).fetchone())
    return row
