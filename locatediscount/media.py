"""Cloudinary-backed storage for user-uploaded product images."""


class MediaStorageError(ValueError):
    """Raised when media cannot be safely persisted or removed."""


def upload_product_image(stream, *, cloud_name, api_key, api_secret, public_id):
    """Upload one validated image and return its durable Cloudinary identifiers."""
    try:
        import cloudinary
        import cloudinary.uploader

        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True,
        )
        result = cloudinary.uploader.upload(
            stream,
            resource_type="image",
            public_id=public_id,
            overwrite=False,
            unique_filename=False,
            use_filename=False,
            tags=["locatediscount", "product"],
        )
    except Exception as error:
        raise MediaStorageError("Cloudinary could not save this image. Please try again.") from error

    secure_url = result.get("secure_url", "")
    stored_public_id = result.get("public_id", "")
    if not secure_url.startswith("https://res.cloudinary.com/") or not stored_public_id:
        raise MediaStorageError("Cloudinary returned an invalid image response.")
    return {"secure_url": secure_url, "public_id": stored_public_id}


def destroy_product_image(*, cloud_name, api_key, api_secret, public_id):
    """Delete an image by public ID, used to compensate for failed DB writes."""
    import cloudinary
    import cloudinary.uploader

    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )
    cloudinary.uploader.destroy(public_id, resource_type="image", invalidate=True)
