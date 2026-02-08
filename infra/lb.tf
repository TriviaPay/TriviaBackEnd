resource "oci_load_balancer_load_balancer" "main" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.resource_prefix}-lb"
  shape          = "flexible"
  subnet_ids     = [oci_core_subnet.public.id]
  is_private     = false

  shape_details {
    minimum_bandwidth_in_mbps = var.lb_min_bandwidth_mbps
    maximum_bandwidth_in_mbps = var.lb_max_bandwidth_mbps
  }

  network_security_group_ids = [oci_core_network_security_group.lb.id]
}

resource "oci_load_balancer_backend_set" "api" {
  load_balancer_id = oci_load_balancer_load_balancer.main.id
  name             = "api-backend"
  policy           = "ROUND_ROBIN"

  health_checker {
    protocol = "HTTP"
    url_path = "/health"
    port     = var.backend_port
    retries  = 3
    timeout_in_millis = 3000
    interval_in_millis = 10000
    return_code = 200
  }
}

resource "oci_load_balancer_listener" "http" {
  load_balancer_id         = oci_load_balancer_load_balancer.main.id
  name                     = "http"
  default_backend_set_name = oci_load_balancer_backend_set.api.name
  port                     = var.lb_listener_port
  protocol                 = "HTTP"
}
