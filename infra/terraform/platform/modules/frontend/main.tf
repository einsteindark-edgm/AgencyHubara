# Frontend de un tenant: bucket S3 PRIVADO (sin acceso público) + CloudFront con
# OAC (Origin Access Control). El SPA enruta client-side, así que 403/404 → /index.html.
# Coincide con INFRASTRUCTURE.md §3.1.

variable "tenant" { type = string }
variable "domain_aliases" { type = list(string) }
variable "acm_certificate_arn" { type = string }
variable "price_class" { type = string }
variable "use_local" {
  type    = bool
  default = false
}

locals {
  bucket_name = "agencyhubara-${var.tenant}-frontend"
  has_cert    = var.acm_certificate_arn != ""
}

resource "aws_s3_bucket" "site" {
  bucket = local.bucket_name
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "site" {
  bucket = aws_s3_bucket.site.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_versioning" "site" {
  bucket = aws_s3_bucket.site.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${local.bucket_name}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# SEC-10: security headers en el edge (CloudFront). HSTS/nosniff/frame-options/
# referrer son seguros (no afectan la SPA). La CSP es compatible con un build de
# Vite (scripts externos 'self'; 'unsafe-inline' SOLO para estilos —
# Tailwind/atributos style). `connect-src https:` deja llamar a la API, Cognito y
# OTel (todos https) sin abrir exfil por http. Verificá la consola del dashboard
# tras el primer deploy; si algo se bloquea, relajá la directiva puntual.
resource "aws_cloudfront_response_headers_policy" "security" {
  name = "agencyhubara-${var.tenant}-security-headers"

  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    content_security_policy {
      override = true
      content_security_policy = join("; ", [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: https:",
        "font-src 'self' data:",
        "connect-src 'self' https:",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
      ])
    }
  }
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = var.price_class
  comment             = "AgencyHubara frontend — ${var.tenant}"
  aliases             = local.has_cert ? var.domain_aliases : []

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "s3-${local.bucket_name}"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id           = "s3-${local.bucket_name}"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }

    min_ttl     = 0
    default_ttl = 3600
    max_ttl     = 86400
  }

  # SPA client-side routing: cualquier ruta desconocida sirve index.html (200).
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = local.has_cert ? null : true
    acm_certificate_arn            = local.has_cert ? var.acm_certificate_arn : null
    ssl_support_method             = local.has_cert ? "sni-only" : null
    minimum_protocol_version       = local.has_cert ? "TLSv1.2_2021" : null
  }
}

# Solo CloudFront (vía OAC) puede leer el bucket. Nadie más.
data "aws_iam_policy_document" "site" {
  statement {
    sid       = "AllowCloudFrontOAC"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site.json
}

output "bucket_name" { value = aws_s3_bucket.site.id }
output "distribution_id" { value = aws_cloudfront_distribution.site.id }
output "distribution_domain_name" { value = aws_cloudfront_distribution.site.domain_name }
