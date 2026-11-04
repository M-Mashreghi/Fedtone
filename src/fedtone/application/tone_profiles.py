"""Bank- and document-type-specific context for tone prompts (Task 7.2).

Every central bank is scored on the same output schema and the same -1 (dovish) .. +1
(hawkish) scale, with the same five dimensions. What differs is the institutional context the
model needs to read the text correctly: the policy instrument, the mandate and target, how the
bank's own concepts map to the shared dimensions, and wording conventions. A profile supplies
that context and nothing else; it never changes the scale, the label bands or the validators.

Calibration limits (also in docs/ARCHITECTURE.md):
- Scores are calibrated within one bank and document type. A +0.3 from the ECB and a +0.3
  from the Fed both mean "moderately hawkish for that institution", not the same policy
  stance; cross-bank level comparisons are descriptive only.
- The profile id is part of the stored prompt version, so analyses made with different
  profiles (or profile versions) are never mixed in deltas or analogue search.
- Changing any profile text requires bumping that profile's version suffix.
"""

from dataclasses import dataclass

GENERIC_ID = "generic-v1"


@dataclass(frozen=True)
class ToneProfile:
    id: str
    institution: str
    document: str
    policy_instrument: str
    mandate: str
    dimension_guidance: tuple[tuple[str, str], ...]
    notes: tuple[str, ...] = ()

    def prompt_section(self) -> str:
        lines = [
            "Institutional context for this document (use it to interpret the text; the scale",
            "and the output schema stay exactly as specified below):",
            f"- Institution: {self.institution}",
            f"- Document: {self.document}",
            f"- Policy instrument: {self.policy_instrument}",
            f"- Mandate and target: {self.mandate}",
            "- How the shared dimensions apply here:",
        ]
        lines += [f"  - {name}: {text}" for name, text in self.dimension_guidance]
        lines += [f"- Note: {note}" for note in self.notes]
        return "\n".join(lines) + "\n"


PROFILES: dict[tuple[str, str], ToneProfile] = {
    ("FED", "fomc_statement"): ToneProfile(
        id="fed-fomc-statement-v1",
        institution="Federal Open Market Committee of the U.S. Federal Reserve",
        document="post-meeting FOMC policy statement",
        policy_instrument="target range for the federal funds rate; balance-sheet runoff",
        mandate="dual mandate of maximum employment and price stability (2% PCE inflation)",
        dimension_guidance=(
            ("inflation", "assessment of inflation and inflation risks relative to 2%"),
            ("labor", "employment and labor-market conditions (part of the mandate)"),
            ("growth", "economic activity and outlook"),
            ("policy_guidance", "rate decision and forward guidance on the rate path"),
            ("balance_sheet", "Treasury and agency MBS holdings, runoff pace"),
        ),
        notes=("Dissents and their direction are informative about the policy bias.",),
    ),
    ("ECB", "monetary_policy_decision"): ToneProfile(
        id="ecb-mp-decision-v1",
        institution="Governing Council of the European Central Bank",
        document="monetary policy decisions press release",
        policy_instrument="three key ECB interest rates (deposit facility rate is the "
        "operational policy rate); APP/PEPP portfolios; refinancing operations",
        mandate="price stability, a symmetric 2% inflation target over the medium term; "
        "no employment mandate",
        dimension_guidance=(
            ("inflation", "headline/underlying inflation and domestic price pressures"),
            ("labor", "wage growth and labour-market strength only as inflation drivers"),
            ("growth", "euro-area activity and outlook"),
            (
                "policy_guidance",
                "rate decision; 'data-dependent, meeting-by-meeting' language "
                "is the ECB's standard neutral guidance, not a dovish signal by itself",
            ),
            ("balance_sheet", "APP/PEPP reinvestment or runoff, TLTRO repayments"),
        ),
        notes=(
            "Fragmentation/transmission-protection language concerns financial "
            "conditions, not the inflation stance.",
        ),
    ),
    ("BOE", "monetary_policy_summary"): ToneProfile(
        id="boe-mps-v1",
        institution="Monetary Policy Committee of the Bank of England",
        document="Monetary Policy Summary",
        policy_instrument="Bank Rate; gilt holdings (quantitative tightening)",
        mandate="2% CPI inflation target set by the government; support for growth and "
        "employment subject to that",
        dimension_guidance=(
            ("inflation", "CPI, services inflation and second-round effects"),
            ("labor", "pay growth and labour-market tightness as inflation persistence"),
            ("growth", "UK activity and outlook"),
            (
                "policy_guidance",
                "Bank Rate decision, vote split, 'restrictive for "
                "sufficiently long' / 'gradual approach' guidance",
            ),
            ("balance_sheet", "gilt stock reduction (QT)"),
        ),
        notes=(
            "The MPC vote split (e.g. 5-4) is informative: members voting for a cut are "
            "the dovish side.",
        ),
    ),
    ("SNB", "monetary_policy_assessment"): ToneProfile(
        id="snb-mpa-v1",
        institution="Governing Board of the Swiss National Bank",
        document="quarterly monetary policy assessment press release",
        policy_instrument="SNB policy rate; foreign exchange market interventions",
        mandate="price stability, defined as CPI inflation between 0% and 2%",
        dimension_guidance=(
            (
                "inflation",
                "inflation relative to the 0-2% range and the conditional inflation forecast",
            ),
            ("labor", "unemployment and labour market"),
            ("growth", "Swiss and global activity"),
            ("policy_guidance", "policy-rate decision and readiness to adjust policy"),
            (
                "balance_sheet",
                "foreign-exchange interventions: willingness to sell foreign "
                "currency tightens, buying foreign currency eases",
            ),
        ),
        notes=(
            "Inflation near the bottom of the 0-2% range is a dovish consideration; a "
            "franc appreciation tightens monetary conditions.",
        ),
    ),
}

# Speeches and minutes (Task 7.3). Their scores form separate baselines from statements.
PROFILES.update(
    {
        ("FED", "speech"): ToneProfile(
            id="fed-speech-v1",
            institution="a member of the Federal Reserve Board or a Reserve Bank president",
            document="public speech by one policymaker (individual view, not a Committee decision)",
            policy_instrument="federal funds rate path and balance sheet, as viewed by the speaker",
            mandate="dual mandate of maximum employment and price stability (2% PCE inflation)",
            dimension_guidance=(
                ("inflation", "the speaker's view of inflation and inflation risks"),
                ("labor", "the speaker's view of the labor market"),
                ("growth", "the speaker's view of activity and the outlook"),
                (
                    "policy_guidance",
                    "the speaker's preferred policy path; use null if the "
                    "speech does not discuss monetary policy",
                ),
                ("balance_sheet", "the speaker's view on balance-sheet policy"),
            ),
            notes=(
                "Many speeches cover regulation, payments or community topics. If the speech "
                "says little about monetary policy, keep scores near 0, lower confidence and "
                "add an uncertainty flag.",
                "Score the speaker's own stance, not the Committee's last decision.",
            ),
        ),
        ("FED", "fomc_minutes"): ToneProfile(
            id="fed-fomc-minutes-v1",
            institution="Federal Open Market Committee of the U.S. Federal Reserve",
            document="minutes of an FOMC meeting, published three weeks after the decision",
            policy_instrument="target range for the federal funds rate; balance-sheet runoff",
            mandate="dual mandate of maximum employment and price stability (2% PCE inflation)",
            dimension_guidance=(
                ("inflation", "participants' assessment of inflation and its risks"),
                ("labor", "participants' assessment of the labor market"),
                ("growth", "staff and participants' outlook for activity"),
                (
                    "policy_guidance",
                    "participants' views on the appropriate policy path, "
                    "including 'many', 'some', 'a few' participants qualifiers",
                ),
                ("balance_sheet", "discussion of balance-sheet runoff and reserves"),
            ),
            notes=(
                "Staff review sections describe data, not policy views; weight the "
                "participants' discussion and the policy action.",
            ),
        ),
        ("BOE", "mpc_minutes"): ToneProfile(
            id="boe-mpc-minutes-v1",
            institution="Monetary Policy Committee of the Bank of England",
            document="minutes of the MPC meeting, including individual members' views",
            policy_instrument="Bank Rate; gilt holdings (quantitative tightening)",
            mandate="2% CPI inflation target set by the government",
            dimension_guidance=(
                ("inflation", "CPI, services inflation and persistence"),
                ("labor", "pay growth and labour-market tightness"),
                ("growth", "UK activity and outlook"),
                (
                    "policy_guidance",
                    "the Committee's decision and the balance of members' votes and stated reasons",
                ),
                ("balance_sheet", "gilt stock reduction (QT)"),
            ),
            notes=(
                "Members' individual statements show the spread of views; the overall score "
                "reflects the Committee as a whole.",
            ),
        ),
    }
)

# Document types scored per speaker; kept out of committee timelines unless asked for.
SPEAKER_DOCUMENT_TYPES = frozenset({"speech"})

GENERIC = ToneProfile(
    id=GENERIC_ID,
    institution="a central bank (no specific profile configured)",
    document="official monetary policy communication",
    policy_instrument="the bank's policy rate and balance-sheet tools",
    mandate="as stated in the document",
    dimension_guidance=(
        ("inflation", "inflation and inflation risks"),
        ("labor", "labour-market conditions"),
        ("growth", "economic activity and outlook"),
        ("policy_guidance", "rate decision and guidance"),
        ("balance_sheet", "asset holdings and balance-sheet policy"),
    ),
    notes=("No calibrated profile exists for this source; treat scores as uncalibrated.",),
)


def profile_for(central_bank: str, document_type: str) -> ToneProfile:
    return PROFILES.get((central_bank, document_type), GENERIC)


def profiled_version(base_version: str, profile: ToneProfile) -> str:
    """Stored prompt version: the shared prompt plus the profile, e.g.
    ``tone-statement-v2+ecb-mp-decision-v1``."""
    return f"{base_version}+{profile.id}"


def profile_id_of(prompt_version: str | None) -> str | None:
    if not prompt_version or "+" not in prompt_version:
        return None
    return prompt_version.split("+", 1)[1]
