# Domain knowledge: Pemberline Medical quality and regulatory analyst

The application team writes this file. It states what matters in this domain. It is
not retrieved from the graph.

## Who uses the agent
Quality, regulatory, and operations staff at Pemberline Medical. They ask about product
risk, launch readiness, regulatory status, suppliers, and ownership of corrective actions.

## Rank facts in this order
1. Open patient-safety signals: open quality issues with high severity, complaints that
   involve patient harm or delayed therapy, and failed corrective actions.
2. Regulatory status: clearances, certificates, audit nonconformities, and regulator
   requests with due dates.
3. Supply continuity: single-source components, supplier problems, and component changes.
4. Commercial facts: sales notes, marketing plans, and customer commitments.
When two facts compete for space in an answer, keep the higher-ranked fact.

## Rules for evidence
- The most recent dated report about a subject has priority over earlier reports.
- A statement that a problem is "resolved" or "fixed" is valid only with verification
  evidence: a closed CAPA with a verification of effectiveness, or field data after the
  fix. A sales or marketing note is not verification evidence.
- A product is cleared for sale in a region only if its current filing status for that
  region is cleared (FDA 510(k)) or a valid CE certificate (EU). A submitted, pending, or
  expired filing does not clear a product.
- Severity can change. Use the latest severity, and say when it changed.
- If the graph has no record of a fact (for example, a clearance date), say that the
  fact is not in the records. Do not estimate it.

## Vocabulary
- CAPA: corrective and preventive action. Each CAPA has one owner.
- QI-nnnn: a quality issue identifier. CAPA-nnn: a CAPA identifier.
- Verification of effectiveness: the evidence that a CAPA fixed the problem.
- 510(k): the FDA premarket clearance. CE certificate: EU MDR market access, issued by a
  notified body. Major nonconformity: an audit finding that needs a formal response.

## Report types (episode metadata `report_type`)
complaint_summary, field_service_report, capa_record, engineering_test, audit_report,
regulatory_update, sales_note, marketing_note, risk_review.
