"""Запустить live evaluation GigaChat без сохранения тестовых данных."""
import uuid
from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.common.enums import Channel
from apps.intake.ai.services import AIExtractionService
from apps.intake.evaluation.runner import evaluate_extraction, load_evaluation_cases
from apps.intake.models import OrderDraftItem
from apps.intake.services import InboundEventService, OrderDraftService


class Command(BaseCommand):
    help = "Run curated order-extraction cases against configured GigaChat."

    def add_arguments(self, parser):
        parser.add_argument("--case", dest="case_id", help="Run one case by ID.")
        parser.add_argument(
            "--fail-under",
            type=float,
            default=0.80,
            help="Required passed-case ratio (default: 0.80).",
        )

    def handle(self, *args, **options):
        cases = load_evaluation_cases()
        if options["case_id"]:
            cases = [case for case in cases if case["id"] == options["case_id"]]
            if not cases:
                raise CommandError(f"Unknown evaluation case: {options['case_id']}")
        if not 0 <= options["fail_under"] <= 1:
            raise CommandError("--fail-under must be between 0 and 1.")

        results = []
        with transaction.atomic():
            for case in cases:
                result = self._run_case(case)
                results.append(result)
                marker = "PASS" if result.passed else "FAIL"
                self.stdout.write(f"[{marker}] {result.case_id}")
                for error in result.errors:
                    self.stdout.write(f"  - {error}")
            transaction.set_rollback(True)

        passed = sum(result.passed for result in results)
        ratio = passed / len(results)
        self.stdout.write(f"Result: {passed}/{len(results)} ({ratio:.0%})")
        self.stdout.write("Evaluation database changes rolled back.")
        if ratio < options["fail_under"]:
            raise CommandError(
                f"GigaChat evaluation score {ratio:.0%} is below "
                f"required {options['fail_under']:.0%}."
            )

    @staticmethod
    def _run_case(case):
        unique = uuid.uuid4().hex
        event = InboundEventService.register(
            channel=Channel.WEBSITE,
            external_event_id=f"evaluation-{unique}",
            external_user_id=f"evaluation-{unique}",
            conversation_key=f"evaluation-{unique}",
            raw_text=case["message"],
        ).event
        draft, _ = OrderDraftService.get_or_create_active(
            channel=event.channel,
            external_user_id=event.external_user_id,
            conversation_key=event.conversation_key,
        )
        Command._apply_draft_context(draft, case.get("draft", {}))
        event.draft = draft
        event.save(update_fields=["draft", "updated_at"])

        current_datetime = datetime(
            2026,
            8,
            24,
            12,
            0,
            tzinfo=timezone.get_current_timezone(),
        )
        extraction, _run = AIExtractionService.extract_with_repair(
            event,
            draft,
            current_datetime=current_datetime,
        )
        return evaluate_extraction(case, extraction)

    @staticmethod
    def _apply_draft_context(draft, context):
        context = dict(context)
        item_contexts = context.pop("items", [])
        for field, value in context.items():
            if field == "total_amount" and value is not None:
                value = Decimal(value)
            setattr(draft, field, value)
        if context:
            draft.save()
        for line_number, item in enumerate(item_contexts, start=1):
            OrderDraftItem.objects.create(
                draft=draft,
                line_number=line_number,
                raw_product_name=item["raw_product_name"],
                requested_quantity=Decimal(str(item["quantity"])),
                requested_unit=item["unit"],
            )
