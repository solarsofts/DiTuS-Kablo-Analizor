"""IEC 60287 soil partial-dryout helpers.

The standard two-zone equation is used only for a single isolated direct-buried
cable representation.  Multi-cable / multi-circuit geometry is handled by the
nonlinear nodal critical-isotherm model instead of extending the literal IEC
formula outside its stated simple-soil scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt

from ucd.models.project import ThermalMaterialData


REFERENCE = (
    "IEC 60287-1-1:2023 partial soil dry-out current-rating equation; "
    "IEC 60287-2-1:2023 thermal-resistance scope"
)


class SoilDryoutInputError(ValueError):
    pass


@dataclass(frozen=True)
class SoilDryoutProfile:
    material_id: str
    material_name: str
    moist_thermal_resistivity_km_w: float
    dry_thermal_resistivity_km_w: float
    critical_temperature_c: float
    resistivity_ratio: float
    data_state: str
    source_reference: str

    @property
    def enabled(self) -> bool:
        return self.critical_temperature_c > 0.0 and self.dry_thermal_resistivity_km_w > 0.0


@dataclass(frozen=True)
class IecDryoutRatingResult:
    ampacity_a: float
    wet_ampacity_a: float
    dryout_active_at_rating: bool
    critical_temperature_rise_c: float
    resistivity_ratio: float
    trace: tuple[str, ...]


def material_dryout_profile(material: ThermalMaterialData) -> SoilDryoutProfile | None:
    critical = float(material.critical_dryout_temperature_c or 0.0)
    dry_rho = float(material.dry_state_thermal_resistivity_km_w or 0.0)
    wet_rho = float(material.thermal_resistivity_km_w or 0.0)
    if critical <= 0.0 and dry_rho <= 0.0:
        return None
    if critical <= 0.0 or dry_rho <= 0.0:
        raise SoilDryoutInputError(
            f"{material.material_id}: kuruma modeli için kritik sıcaklık ve kuru-durum ısıl özdirenci birlikte gereklidir."
        )
    if wet_rho <= 0.0:
        raise SoilDryoutInputError(f"{material.material_id}: nemli-durum ısıl özdirenci pozitif olmalıdır.")
    if dry_rho <= wet_rho:
        raise SoilDryoutInputError(
            f"{material.material_id}: kuru-durum ısıl özdirenci nemli-durum değerinden büyük olmalıdır."
        )
    if not all(isfinite(v) for v in (critical, dry_rho, wet_rho)):
        raise SoilDryoutInputError(f"{material.material_id}: kuruma parametreleri sonlu olmalıdır.")
    return SoilDryoutProfile(
        material.material_id,
        material.name,
        wet_rho,
        dry_rho,
        critical,
        dry_rho / wet_rho,
        str(material.data_state),
        str(material.source_reference),
    )


def validate_dryout_against_ambient(profile: SoilDryoutProfile, ambient_temperature_c: float) -> float:
    ambient = float(ambient_temperature_c)
    rise = profile.critical_temperature_c - ambient
    if rise <= 0.0:
        raise SoilDryoutInputError(
            f"{profile.material_id}: kritik kuruma sıcaklığı ({profile.critical_temperature_c:.3f} °C) "
            f"ortam sıcaklığından ({ambient:.3f} °C) büyük olmalıdır."
        )
    return rise


def iec_two_zone_ampacity(
    *,
    delta_theta_c: float,
    dielectric_loss_w_m: float,
    t1_km_w: float,
    t2_km_w: float,
    t3_km_w: float,
    t4_moist_km_w: float,
    ac_resistance_ohm_m: float,
    conductors_per_cable: int,
    lambda1: float,
    lambda2: float,
    profile: SoilDryoutProfile,
    ambient_temperature_c: float,
    wet_ampacity_a: float,
) -> IecDryoutRatingResult:
    """Return IEC two-zone partial-dryout rating for a simple isolated cable.

    T4 must be the moist-soil external resistance.  This helper intentionally
    does not attempt mutual-heating or multi-circuit corrections.
    """

    n = int(conductors_per_cable)
    if n < 1:
        raise SoilDryoutInputError("Kablo başına iletken sayısı en az 1 olmalıdır.")
    if ac_resistance_ohm_m <= 0.0:
        raise SoilDryoutInputError("AC iletken direnci pozitif olmalıdır.")
    if t4_moist_km_w <= 0.0:
        raise SoilDryoutInputError("Nemli zemin T4 değeri pozitif olmalıdır.")
    critical_rise = validate_dryout_against_ambient(profile, ambient_temperature_c)
    v = profile.resistivity_ratio

    # At the wet-soil rating, test the cable/soil interface temperature.  If it
    # remains below the critical isotherm, no dry zone is present and the wet
    # solution remains binding.
    wet_external_heat = (
        wet_ampacity_a**2 * ac_resistance_ohm_m * n * (1.0 + lambda1 + lambda2)
        + dielectric_loss_w_m * n
    )
    wet_interface_temperature = ambient_temperature_c + wet_external_heat * t4_moist_km_w
    if wet_interface_temperature <= profile.critical_temperature_c + 1e-9:
        return IecDryoutRatingResult(
            wet_ampacity_a,
            wet_ampacity_a,
            False,
            critical_rise,
            v,
            (
                f"IEC iki-bölge kuruma kontrolü: kablo/zemin ara yüzü {wet_interface_temperature:.3f} °C "
                f"<= kritik {profile.critical_temperature_c:.3f} °C; kuruma etkin değil.",
            ),
        )

    numerator = (
        float(delta_theta_c)
        - dielectric_loss_w_m * (0.5 * t1_km_w + n * (t2_km_w + t3_km_w + v * t4_moist_km_w))
        + (v - 1.0) * critical_rise
    )
    denominator = ac_resistance_ohm_m * (
        t1_km_w
        + n * (1.0 + lambda1) * t2_km_w
        + n * (1.0 + lambda1 + lambda2) * (t3_km_w + v * t4_moist_km_w)
    )
    if numerator <= 0.0:
        raise SoilDryoutInputError("Kısmi kuruma denkleminde kullanılabilir sıcaklık artışı pozitif değil.")
    if denominator <= 0.0:
        raise SoilDryoutInputError("Kısmi kuruma denkleminde ısıl/elektriksel payda pozitif değil.")
    rating = sqrt(numerator / denominator)
    return IecDryoutRatingResult(
        rating,
        wet_ampacity_a,
        True,
        critical_rise,
        v,
        (
            f"IEC iki-bölge kuruma etkin: rho_dry/rho_moist={v:.6f}; "
            f"theta_x={profile.critical_temperature_c:.3f} °C; Δtheta_x={critical_rise:.3f} K.",
            f"Nemli rating={wet_ampacity_a:.3f} A; kuruma-düzeltilmiş rating={rating:.3f} A.",
        ),
    )


def iec_two_zone_temperature_residual(
    temperature_c: float,
    *,
    ambient_temperature_c: float,
    current_a: float,
    ac_resistance_ohm_m: float,
    dielectric_loss_w_m: float,
    t1_km_w: float,
    t2_km_w: float,
    t3_km_w: float,
    t4_moist_km_w: float,
    conductors_per_cable: int,
    lambda1: float,
    lambda2: float,
    profile: SoilDryoutProfile,
) -> float:
    """Residual of the IEC two-zone conductor-temperature equation."""

    critical_rise = validate_dryout_against_ambient(profile, ambient_temperature_c)
    n = int(conductors_per_cable)
    v = profile.resistivity_ratio
    chain = (
        t1_km_w
        + n * (1.0 + lambda1) * t2_km_w
        + n * (1.0 + lambda1 + lambda2) * (t3_km_w + v * t4_moist_km_w)
    )
    dielectric_chain = 0.5 * t1_km_w + n * (t2_km_w + t3_km_w + v * t4_moist_km_w)
    predicted = (
        ambient_temperature_c
        + current_a**2 * ac_resistance_ohm_m * chain
        + dielectric_loss_w_m * dielectric_chain
        - (v - 1.0) * critical_rise
    )
    return predicted - float(temperature_c)
