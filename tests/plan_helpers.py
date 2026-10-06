"""Change a plan's features the way an admin does on /admin/pricing (a license_plans row)."""
from licensing import get_license_plan, refresh_license_plan_cache
from models import LicensePlanConfig, db


def set_plan_features(app, key, *, remove=(), add=()):
    with app.app_context():
        plan = get_license_plan(key)
        features = sorted((set(plan.features) - set(remove)) | set(add))
        row = LicensePlanConfig.query.filter_by(key=key).first()
        if row is None:
            row = LicensePlanConfig(
                key=key, label=plan.label, price_usd_cents=int(round(plan.price_usd * 100)),
                duration_days=plan.duration_days, storage_limit_bytes=plan.storage_limit_bytes,
                is_purchasable=plan.is_purchasable, max_models_per_project=plan.max_models_per_project,
            )
            db.session.add(row)
        row.features = features
        db.session.commit()
        refresh_license_plan_cache()
