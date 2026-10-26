"""
backend/agents/procurement_agent.py
-------------------------------------
Module 4 — Procurement Agent used by the native orchestration service.

Receives an inventory assessment result via OrchestrationState and produces a
structured Top-2 procurement recommendation based on Lead-Time-Based Supplier
Ranking.

Ranking algorithm
-----------------
1. Retrieve all eligible suppliers for the product from product_supplier
   (which now contains supplier alternatives loaded from
   module4_all_supplier_lead_time_ranking.csv).
2. Sort eligible suppliers by ascending lead_time_days (from supplier master).
3. If multiple suppliers share the same lead time, use ascending supplier_id
   as a deterministic secondary sort key.
4. Return the Top 2 ranked suppliers.
5. If fewer than 2 exist, return what is available with an explicit status.
6. If none exist, return a no_supplier_available result.

Explicit exclusions (enforced by design)
-----------------------------------------
- Does NOT place purchase orders.
- Does NOT modify stock quantities.
- Does NOT overwrite Module 3's routing_decision or assessment fields.
- Does NOT implement TOPSIS, cost optimisation, or supplier-quality scoring.
- Does NOT claim lead time is a proxy for overall supplier quality.
- Does NOT fabricate purchase rates, damage rates, or waste rates.
- Manager approval is ALWAYS required.

Tie-handling
------------
When multiple suppliers share the shortest lead time, supplier_id is used as a
deterministic tie-breaker. The response clearly reports how many suppliers tied
and states that the tie-breaker is supplier_id ordering — not operational merit.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

AGENT_NAME = "procurement_agent"
RANKING_METHOD = "lead_time_based"
POLICY_VERSION = "v1.0"


def _has_valid_lead_time(supplier: dict) -> bool:
    """Only positive integer supplier lead times are eligible for ranking."""
    lead_time = supplier.get("lead_time_days")
    return isinstance(lead_time, int) and not isinstance(lead_time, bool) and lead_time > 0


def _get_eligible_suppliers(conn, product_id: str) -> list[dict]:
    """
    Retrieve all suppliers linked to product_id via product_supplier,
    joined with supplier master for lead_time_days and supplier_name.

    Returns a list of dicts with keys:
        supplier_id, supplier_name, lead_time_days
    Sorted by lead_time_days ASC, supplier_id ASC (deterministic).
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                s.supplier_id,
                s.supplier_name,
                s.lead_time_days
            FROM product_supplier ps
            JOIN supplier s ON ps.supplier_id = s.supplier_id
            WHERE ps.product_id = %s
            ORDER BY s.lead_time_days ASC, s.supplier_id ASC
            """,
            (product_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _get_product_name(conn, product_id: str) -> Optional[str]:
    """Return product_name for the given product_id, or None."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT product_name FROM product WHERE product_id = %s",
            (product_id,),
        )
        row = cur.fetchone()
        return row["product_name"] if row else None
    finally:
        cur.close()


def _build_explanation(rank: int, supplier_name: str, lead_time: int,
                        is_tied: bool, tie_count: int) -> str:
    """
    Build an honest, factual explanation for a ranked recommendation.
    Does not claim optimality beyond lead-time ordering.
    """
    ordinals = {1: "first", 2: "second"}
    ordinal = ordinals.get(rank, f"rank-{rank}")

    if rank == 1:
        if is_tied and tie_count > 1:
            return (
                f"{supplier_name} is ranked {ordinal} because its recorded lead time "
                f"({lead_time} day{'s' if lead_time != 1 else ''}) is the shortest among "
                f"the eligible suppliers for this product. Note: {tie_count} suppliers share "
                f"this lead time; supplier_id ordering was used as a deterministic tie-breaker, "
                f"not as evidence of operational superiority."
            )
        else:
            return (
                f"{supplier_name} is ranked {ordinal} because its recorded lead time "
                f"({lead_time} day{'s' if lead_time != 1 else ''}) is shorter than the other "
                f"eligible suppliers for this product."
            )
    elif rank == 2:
        if is_tied and tie_count > 1:
            return (
                f"{supplier_name} is ranked {ordinal} based on its recorded lead time "
                f"({lead_time} day{'s' if lead_time != 1 else ''}). Supplier_id ordering "
                f"determined position among suppliers with the same lead time."
            )
        else:
            return (
                f"{supplier_name} is ranked {ordinal} based on its recorded lead time "
                f"({lead_time} day{'s' if lead_time != 1 else ''}), which is the "
                f"second-shortest among eligible suppliers for this product."
            )
    else:
        return (
            f"{supplier_name} is ranked {ordinal} by ascending lead time "
            f"({lead_time} day{'s' if lead_time != 1 else ''})."
        )


def _persist_recommendation(conn, rec: dict) -> Optional[int]:
    """
    Persist a procurement recommendation to procurement_recommendation table.
    Returns the new recommendation_id, or None on failure (non-fatal).
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO procurement_recommendation (
                product_id, inventory_assessment_id,
                suggested_replenishment_qty,
                ranking_method, eligible_supplier_count,
                recommendation_status,
                rank1_supplier_id, rank1_supplier_name,
                rank1_lead_time_days, rank1_explanation,
                rank2_supplier_id, rank2_supplier_name,
                rank2_lead_time_days, rank2_explanation,
                has_lead_time_tie, tie_supplier_count, tie_explanation,
                requires_manager_approval, data_warnings, policy_version
            ) VALUES (
                %(product_id)s, %(inventory_assessment_id)s,
                %(suggested_replenishment_qty)s,
                %(ranking_method)s, %(eligible_supplier_count)s,
                %(recommendation_status)s,
                %(rank1_supplier_id)s, %(rank1_supplier_name)s,
                %(rank1_lead_time_days)s, %(rank1_explanation)s,
                %(rank2_supplier_id)s, %(rank2_supplier_name)s,
                %(rank2_lead_time_days)s, %(rank2_explanation)s,
                %(has_lead_time_tie)s, %(tie_supplier_count)s, %(tie_explanation)s,
                TRUE, %(data_warnings)s, %(policy_version)s
            )
            RETURNING recommendation_id
            """,
            rec,
        )
        row = cur.fetchone()
        conn.commit()
        return int(row["recommendation_id"]) if row else None
    except Exception as e:
        conn.rollback()
        logger.warning(f"[ProcurementAgent] Failed to persist recommendation: {e}")
        return None
    finally:
        cur.close()


def run_procurement_agent(conn, state: dict) -> dict:
    """
    Execute the procurement agent for the product in the supplied state.

    Parameters
    ----------
    conn : psycopg2 connection
    state : OrchestrationState dict

    Returns
    -------
    Partial state update dict containing:
        procurement_result : dict (ProcurementResult fields)
        data_notices       : list (appended, not replaced)
    """
    product_id = state.get("product_id", "UNKNOWN")
    assessment = state.get("assessment") or {}
    data_notices = list(state.get("data_notices", []))
    assessment_id = assessment.get("assessment_id")

    suggested_qty = assessment.get("suggested_replenishment_qty") or 0

    logger.info(
        f"[ProcurementAgent] Evaluating product={product_id} "
        f"qty={suggested_qty} assessment_id={assessment_id}"
    )

    # ── Validate inputs ────────────────────────────────────────────────────────
    if not product_id or product_id == "UNKNOWN":
        return {
            "procurement_result": {
                "agent": AGENT_NAME,
                "status": "error",
                "product_id": product_id,
                "product_name": None,
                "suggested_replenishment_qty": suggested_qty,
                "ranking_method": RANKING_METHOD,
                "eligible_supplier_count": 0,
                "ranked_suppliers": [],
                "has_lead_time_tie": False,
                "tie_supplier_count": None,
                "tie_explanation": None,
                "recommendation_status": "error",
                "requires_manager_approval": True,
                "data_warnings": "Missing product_id.",
                "recommendation_notes": "Invalid product_id — cannot proceed.",
                "warning": "Missing product_id.",
            },
            "data_notices": data_notices,
        }

    if suggested_qty is not None and suggested_qty < 0:
        data_notices.append(
            f"PROCUREMENT WARNING: Invalid replenishment quantity ({suggested_qty}) "
            f"for {product_id}. Quantity must be >= 0."
        )
        suggested_qty = 0

    try:
        # ── Retrieve product name ──────────────────────────────────────────────
        product_name = _get_product_name(conn, product_id)

        # ── Retrieve eligible suppliers ────────────────────────────────────────
        supplier_rows = _get_eligible_suppliers(conn, product_id)
        suppliers = [supplier for supplier in supplier_rows if _has_valid_lead_time(supplier)]
        invalid_lead_time_count = len(supplier_rows) - len(suppliers)
        suppliers.sort(key=lambda supplier: (
            supplier["lead_time_days"],
            supplier["supplier_id"],
        ))
        eligible_count = len(suppliers)

        if invalid_lead_time_count:
            invalid_notice = (
                f"PROCUREMENT: Excluded {invalid_lead_time_count} supplier "
                f"alternative(s) for {product_id} because lead_time_days was "
                "missing or invalid; no value was inferred."
            )
            data_notices.append(invalid_notice)

        logger.info(
            f"[ProcurementAgent] product={product_id} "
            f"eligible_suppliers={eligible_count}"
        )

        # ── No suppliers case ──────────────────────────────────────────────────
        if eligible_count == 0:
            data_notices.append(
                f"PROCUREMENT: No supplier alternatives found for {product_id}. "
                "Manual procurement required. Manager approval required."
            )
            result = {
                "agent": AGENT_NAME,
                "status": "no_eligible_supplier",
                "recommendation_status": "no_supplier_available",
                "product_id": product_id,
                "product_name": product_name,
                "suggested_replenishment_qty": suggested_qty,
                "ranking_method": RANKING_METHOD,
                "eligible_supplier_count": 0,
                "ranked_suppliers": [],
                "has_lead_time_tie": False,
                "tie_supplier_count": None,
                "tie_explanation": None,
                "requires_manager_approval": True,
                "data_warnings": (
                    "No supplier with a valid lead time found for this product."
                ),
                "recommendation_notes": (
                    "No supplier with a valid lead time found for this product. "
                    "Manual procurement required."
                ),
                "warning": (
                    "No eligible supplier with a valid lead time. "
                    "Manual procurement required."
                ),
            }
            return {"procurement_result": result, "data_notices": data_notices}

        # ── Tie analysis ───────────────────────────────────────────────────────
        min_lead = suppliers[0]["lead_time_days"]
        tied_at_min = [s for s in suppliers if s["lead_time_days"] == min_lead]
        tie_count = len(tied_at_min)
        is_tied = tie_count > 1

        tie_explanation: Optional[str] = None
        if is_tied:
            tied_names = ", ".join(s["supplier_name"] for s in tied_at_min)
            tie_explanation = (
                f"{tie_count} suppliers share the shortest lead time of {min_lead} "
                f"day{'s' if min_lead != 1 else ''} for this product "
                f"({tied_names}). "
                f"Supplier_id was used as a deterministic tie-breaker to establish "
                f"rank order. This does NOT indicate that the higher-ranked supplier "
                f"is operationally superior."
            )
            data_notices.append(
                f"PROCUREMENT TIE: {tie_count} suppliers share minimum lead time "
                f"({min_lead}d) for {product_id}. Supplier_ID breaks ties. "
                f"This is not evidence of operational quality difference."
            )

        # ── Build ranked list (Top 2) ──────────────────────────────────────────
        top2 = suppliers[:2]
        ranked_suppliers = []
        for rank_idx, sup in enumerate(top2, start=1):
            explanation = _build_explanation(
                rank=rank_idx,
                supplier_name=sup["supplier_name"],
                lead_time=sup["lead_time_days"],
                is_tied=is_tied,
                tie_count=tie_count,
            )
            ranked_suppliers.append({
                "rank": rank_idx,
                "supplier_id": sup["supplier_id"],
                "supplier_name": sup["supplier_name"],
                "lead_time_days": sup["lead_time_days"],
                "explanation": explanation,
            })

        # ── Determine status ───────────────────────────────────────────────────
        if eligible_count >= 2:
            recommendation_status = "top2_available"
        else:
            recommendation_status = "only_one_available"

        # ── Data warnings ──────────────────────────────────────────────────────
        data_warnings_parts = []
        if eligible_count == 1:
            data_warnings_parts.append(
                "Only 1 eligible supplier found. A second recommendation is not available."
            )
        if invalid_lead_time_count:
            data_warnings_parts.append(
                f"{invalid_lead_time_count} supplier alternative(s) were excluded "
                "because lead_time_days was missing or invalid."
            )
        data_warnings_parts.append(
            "Lead-time data is sourced from the supplier master (modelled synthetic values). "
            "Verify with actual supplier contracts before procurement."
        )
        data_warnings_str = "; ".join(data_warnings_parts)

        # ── Compose notes ──────────────────────────────────────────────────────
        rank1 = ranked_suppliers[0]
        notes_parts = [
            f"Ranking method: lead_time_based. "
            f"Eligible suppliers considered: {eligible_count}. "
            f"Rank 1: {rank1['supplier_name']} (lead={rank1['lead_time_days']}d)."
        ]
        if len(ranked_suppliers) >= 2:
            rank2 = ranked_suppliers[1]
            notes_parts.append(
                f"Rank 2: {rank2['supplier_name']} (lead={rank2['lead_time_days']}d)."
            )
        if is_tied:
            notes_parts.append(
                f"Tie: {tie_count} suppliers share shortest lead time. "
                "Supplier_ID used as deterministic tie-breaker."
            )
        notes_parts.append("Manager approval required before any procurement action.")
        recommendation_notes = " ".join(notes_parts)

        data_notices.append(
            "PROCUREMENT: Suggested order quantity derived from Module 3 assessment. "
            "Verify current demand before placing any order. "
            "Manager approval is mandatory — no automatic purchasing occurs."
        )

        # ── Build result dict ─────────────────────────────────────────────────
        r1 = ranked_suppliers[0] if len(ranked_suppliers) >= 1 else None
        r2 = ranked_suppliers[1] if len(ranked_suppliers) >= 2 else None

        result = {
            "agent": AGENT_NAME,
            "status": "recommendation_ready",
            "recommendation_status": recommendation_status,
            "product_id": product_id,
            "product_name": product_name,
            "suggested_replenishment_qty": suggested_qty,
            "ranking_method": RANKING_METHOD,
            "eligible_supplier_count": eligible_count,
            "ranked_suppliers": ranked_suppliers,
            "has_lead_time_tie": is_tied,
            "tie_supplier_count": tie_count if is_tied else None,
            "tie_explanation": tie_explanation,
            "requires_manager_approval": True,
            "data_warnings": data_warnings_str,
            "recommendation_notes": recommendation_notes,
            "warning": None,
            # Backward-compatible flat fields (rank 1 info for existing consumers)
            "recommended_supplier_id": r1["supplier_id"] if r1 else None,
            "recommended_supplier_name": r1["supplier_name"] if r1 else None,
            "supplier_lead_time_days": r1["lead_time_days"] if r1 else None,
            # rank2 flat fields
            "rank2_supplier_id": r2["supplier_id"] if r2 else None,
            "rank2_supplier_name": r2["supplier_name"] if r2 else None,
            "rank2_lead_time_days": r2["lead_time_days"] if r2 else None,
            # Retained for compatibility; lead-time ranking does not calculate a score.
            "topsis_score": None,
        }

        # ── Persist to DB (non-fatal if it fails) ────────────────────────────
        persist_payload = {
            "product_id": product_id,
            "inventory_assessment_id": assessment_id,
            "suggested_replenishment_qty": suggested_qty,
            "ranking_method": RANKING_METHOD,
            "eligible_supplier_count": eligible_count,
            "recommendation_status": recommendation_status,
            "rank1_supplier_id": r1["supplier_id"] if r1 else None,
            "rank1_supplier_name": r1["supplier_name"] if r1 else None,
            "rank1_lead_time_days": r1["lead_time_days"] if r1 else None,
            "rank1_explanation": r1["explanation"] if r1 else None,
            "rank2_supplier_id": r2["supplier_id"] if r2 else None,
            "rank2_supplier_name": r2["supplier_name"] if r2 else None,
            "rank2_lead_time_days": r2["lead_time_days"] if r2 else None,
            "rank2_explanation": r2["explanation"] if r2 else None,
            "has_lead_time_tie": is_tied,
            "tie_supplier_count": tie_count if is_tied else None,
            "tie_explanation": tie_explanation,
            "data_warnings": data_warnings_str,
            "policy_version": POLICY_VERSION,
        }
        rec_id = _persist_recommendation(conn, persist_payload)
        if rec_id:
            result["recommendation_id"] = rec_id
            logger.info(
                f"[ProcurementAgent] Persisted recommendation_id={rec_id} "
                f"for product={product_id}"
            )
        else:
            logger.warning(
                f"[ProcurementAgent] Recommendation not persisted for {product_id} "
                "(procurement_recommendation table may not exist yet)"
            )

        return {"procurement_result": result, "data_notices": data_notices}

    except Exception as e:
        logger.error(
            f"[ProcurementAgent] Error for {product_id}: {e}", exc_info=True
        )
        return {
            "procurement_result": {
                "agent": AGENT_NAME,
                "status": "error",
                "recommendation_status": "error",
                "product_id": product_id,
                "product_name": None,
                "suggested_replenishment_qty": suggested_qty,
                "ranking_method": RANKING_METHOD,
                "eligible_supplier_count": 0,
                "ranked_suppliers": [],
                "has_lead_time_tie": False,
                "tie_supplier_count": None,
                "tie_explanation": None,
                "requires_manager_approval": True,
                "data_warnings": str(e),
                "recommendation_notes": f"Agent error: {e}",
                "warning": str(e),
                "recommended_supplier_id": None,
                "recommended_supplier_name": None,
                "supplier_lead_time_days": None,
                "rank2_supplier_id": None,
                "rank2_supplier_name": None,
                "rank2_lead_time_days": None,
                "topsis_score": None,
            },
            "data_notices": data_notices,
        }
