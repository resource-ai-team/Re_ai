"""Photos produce observations, never measured mass fractions."""


class UnavailablePhotoObserver:
    def observe(self, content, media_type, *, external_allowed=False):
        return {
            "status": "photo_provider_not_configured",
            "observations": [],
            "external_transmission": False,
            "quantitative_measurements": [],
        }


class MockPhotoObserver:
    def observe(self, content, media_type, *, external_allowed=False):
        return {
            "status": "synthetic_observation",
            "synthetic": True,
            "observations": ["표면에 종이 라벨로 보이는 물체"],
            "requires_user_confirmation": True,
            "external_transmission": False,
            "quantitative_measurements": [],
        }
