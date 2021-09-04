# Copyright 2014-2018 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, exceptions, fields, models, _
from odoo.exceptions import UserError


class Settlement(models.Model):
    _name = "sale.commission.settlement"
    _description = "Settlement"

    def _default_currency(self):
        return self.env.user.company_id.currency_id.id

    name = fields.Char('Name')
    total = fields.Float(compute="_compute_total", readonly=True, store=True)
    date_from = fields.Date(string="From")
    date_to = fields.Date(string="To")
    agent = fields.Many2one(
        comodel_name="res.partner", domain="[('agent', '=', True)]")
    agent_type = fields.Selection(related='agent.agent_type')
    lines = fields.One2many(
        comodel_name="sale.commission.settlement.line",
        inverse_name="settlement", string="Settlement lines", readonly=True)
    state = fields.Selection(
        selection=[("settled", "Settled"),
                   ("invoiced", "Invoiced"),
                   ("cancel", "Canceled"),
                   ("except_invoice", "Invoice exception")], string="State",
        readonly=True, default="settled")
    invoice = fields.Many2one(
        comodel_name="account.invoice", string="Generated invoice",
        readonly=True)
    # origin = fields.Char(string="Origin")
    currency_id = fields.Many2one(
        comodel_name='res.currency', readonly=True,
        default=_default_currency)
    company_id = fields.Many2one(
        comodel_name='res.company',
        default=lambda self: self.env.user.company_id,
        required=True
    )

    @api.depends('lines', 'lines.settled_amount')
    def _compute_total(self):
        for record in self:
            record.total = sum(x.settled_amount for x in record.lines)

    @api.multi
    def action_cancel(self):
        if any(x.state != 'settled' for x in self):
            raise exceptions.Warning(
                _('Cannot cancel an invoiced settlement.'))
        self.write({'state': 'cancel'})

    @api.multi
    def unlink(self):
        """Allow to delete only cancelled settlements"""
        if any(x.state == 'invoiced' for x in self):
            raise exceptions.Warning(
                _("You can't delete invoiced settlements."))
        return super(Settlement, self).unlink()

    @api.multi
    def action_invoice(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Make invoice'),
            'res_model': 'sale.commission.make.invoice',
            'view_type': 'form',
            'target': 'new',
            'view_mode': 'form',
            'context': {'settlement_ids': self.ids}
        }

    def _prepare_invoice_header(self, settlement, journal, date=False):
        invoice = self.env['account.invoice'].new({
            'partner_id': settlement.agent.id,
            'type': ('in_invoice' if journal.type == 'purchase' else
                     'in_refund'),
            'date_invoice': date,
            'journal_id': journal.id,
            'company_id': settlement.company_id.id,
            'state': 'draft',
        })
        # Get other invoice values from onchanges
        invoice._onchange_partner_id()
        invoice._onchange_journal_id()
        return invoice._convert_to_write(invoice._cache)

    def _prepare_invoice_line(self, settlement, invoice, product):
        invoice_line = self.env['account.invoice.line'].new({
            'invoice_id': invoice.id,
            'product_id': product.id,
            'quantity': 1,
        })
        # Get other invoice line values from product onchange

        invoice_line._onchange_product_id()
        invoice_line_vals = invoice_line._convert_to_write(invoice_line._cache)
        # Put commission fee
        if invoice.type == 'in_refund':
            invoice_line_vals['price_unit'] = -settlement.total
        else:
            invoice_line_vals['price_unit'] = settlement.total
        # Put period string
        lang = self.env['res.lang'].search(
            [('code', '=', invoice.partner_id.lang or
              self.env.context.get('lang', 'en_US'))])
        date_from = fields.Date.from_string(settlement.date_from)
        date_to = fields.Date.from_string(settlement.date_to)
        invoice_line_vals['name'] += "\n" + _('Period: from %s to %s') % (
            date_from.strftime(lang.date_format),
            date_to.strftime(lang.date_format))
        return invoice_line_vals

    def _add_extra_invoice_lines(self, settlement):
        """Hook for adding extra invoice lines.
        :param settlement: Source settlement.
        :return: List of dictionaries with the extra lines.
        """
        return []
        
    def create_invoice_header(self, journal, date):
        """Hook that can be used in order to group invoices or
        find open invoices
        """
        invoice_vals = self._prepare_invoice_header(self, journal, date=date)
        return self.env['account.invoice'].create(invoice_vals)

    @api.multi
    def make_invoices(self, journal, product, date=False):
        invoice_line_obj = self.env['account.invoice.line']
        for settlement in self:
            # select the proper journal according to settlement's amount
            # considering _add_extra_invoice_lines sum of values
            extra_invoice_lines = self._add_extra_invoice_lines(settlement)
            invoice = settlement.create_invoice_header(journal, date)
            invoice_line_vals = self._prepare_invoice_line(
                settlement, invoice, product)
            invoice_line_obj.create(invoice_line_vals)
            invoice.compute_taxes()
            for invoice_line_vals in extra_invoice_lines:
                invoice_line_obj.create(invoice_line_vals)
            settlement.write({
                'state': 'invoiced',
                'invoice': invoice.id,
            })
        if self.env.context.get('no_check_negative', False):
            return
        for settlement in self:
            if settlement.invoice.amount_total < 0:
                raise UserError(_('Value cannot be negative'))


class SettlementLine(models.Model):
    _name = "sale.commission.settlement.line"
    _description = "Line of a commission settlement"

    settlement = fields.Many2one(
        "sale.commission.settlement", readonly=True, ondelete="cascade",
        required=True)
    agent_line = fields.Many2many(
        comodel_name='account.invoice.line.agent',
        relation='settlement_agent_line_rel', column1='settlement_id',
        column2='agent_line_id', required=True)
    date = fields.Date(related="agent_line.invoice_date", store=True)
    invoice_line = fields.Many2one(
        comodel_name='account.invoice.line', store=True,
        related='agent_line.object_id')
    invoice = fields.Many2one(
        comodel_name='account.invoice', store=True, string="Invoice",
        related='invoice_line.invoice_id')
    origin = fields.Char(
        string="Origin", store=True,
        related='invoice_line.origin') 
    customer = fields.Char(
        string="Customer", store=True,
        related='invoice_line.partner_id.name'
    )     
    agent = fields.Many2one(
        comodel_name="res.partner", readonly=True, related="agent_line.agent",
        store=True)
    settled_amount = fields.Monetary(
        related="agent_line.amount", readonly=True, store=True)
        
    currency_id = fields.Many2one(
        related="agent_line.currency_id",
        store=True,
        readonly=True,
    )
    commission = fields.Many2one(
        comodel_name="sale.commission", related="agent_line.commission")
    company_id = fields.Many2one(
        comodel_name='res.company',
        related='settlement.company_id',
    )

    @api.depends('settlement.lines')
    def _compute_commission_total(self):
        for record in self:
            record.commission_total = 0.0
            for line in record.agent_line:
                record.commission_total += sum(x.amount for x in line)

    # commission_total = fields.Float(
    #     string="Commissions",
    #     compute="_compute_commission_total",
    #     store=True,
    #     )            
    comm_total = fields.Float(
        comodel_name='invoice_vals.commission_total',
        string="Comissão Total", store=True,
        related='invoice.commission_total'
        )   

    @api.constrains('settlement', 'agent_line')
    def _check_company(self):
        for record in self:
            for line in record.agent_line:
                if line.company_id != record.company_id:
                    raise UserError(_("Company must be the same"))

####################################
#     def _query(self, with_clause='', fields={}, groupby='', from_clause=''):
#         with_ = ("WITH %s" % with_clause) if with_clause else ""

#         select_ = """
#             min(l.id) as id,
#             l.product_id as product_id,
#             t.uom_id as product_uom,
#             sum(l.product_uom_qty / u.factor * u2.factor) as product_uom_qty,
#             sum(l.qty_delivered / u.factor * u2.factor) as qty_delivered,
#             sum(l.qty_invoiced / u.factor * u2.factor) as qty_invoiced,
#             sum(l.qty_to_invoice / u.factor * u2.factor) as qty_to_invoice,
#             sum(l.price_total / CASE COALESCE(s.currency_rate, 0) WHEN 0 THEN 1.0 ELSE s.currency_rate END) as price_total,
#             sum(l.price_subtotal / CASE COALESCE(s.currency_rate, 0) WHEN 0 THEN 1.0 ELSE s.currency_rate END) as price_subtotal,
#             sum(l.untaxed_amount_to_invoice / CASE COALESCE(s.currency_rate, 0) WHEN 0 THEN 1.0 ELSE s.currency_rate END) as untaxed_amount_to_invoice,
#             sum(l.untaxed_amount_invoiced / CASE COALESCE(s.currency_rate, 0) WHEN 0 THEN 1.0 ELSE s.currency_rate END) as untaxed_amount_invoiced,
#             count(*) as nbr,
#             s.name as name,
#             s.date_order as date,
#             s.confirmation_date as confirmation_date,
#             s.state as state,
#             s.partner_id as partner_id,
#             s.user_id as user_id,
#             s.company_id as company_id,
#             extract(epoch from avg(date_trunc('day',s.date_order)-date_trunc('day',s.create_date)))/(24*60*60)::decimal(16,2) as delay,
#             t.categ_id as categ_id,
#             s.pricelist_id as pricelist_id,
#             s.analytic_account_id as analytic_account_id,
#             s.team_id as team_id,
#             p.product_tmpl_id,
#             partner.country_id as country_id,
#             partner.commercial_partner_id as commercial_partner_id,
#             sum(p.weight * l.product_uom_qty / u.factor * u2.factor) as weight,
#             sum(p.volume * l.product_uom_qty / u.factor * u2.factor) as volume,
#             l.discount as discount,
#             sum((l.price_unit * l.product_uom_qty * l.discount / 100.0 / CASE COALESCE(s.currency_rate, 0) WHEN 0 THEN 1.0 ELSE s.currency_rate END)) as discount_amount,
#             s.id as order_id
#         """

#         for field in fields.values():
#             select_ += field

#         from_ = """
#                 sale_order_line l
#                       join sale_order s on (l.order_id=s.id)
#                       join res_partner partner on s.partner_id = partner.id
#                         left join product_product p on (l.product_id=p.id)
#                             left join product_template t on (p.product_tmpl_id=t.id)
#                     left join uom_uom u on (u.id=l.product_uom)
#                     left join uom_uom u2 on (u2.id=t.uom_id)
#                     left join product_pricelist pp on (s.pricelist_id = pp.id)
#                 %s
#         """ % from_clause

#         groupby_ = """
#             l.product_id,
#             l.order_id,
#             t.uom_id,
#             t.categ_id,
#             s.name,
#             s.date_order,
#             s.confirmation_date,
#             s.partner_id,
#             s.user_id,
#             s.state,
#             s.company_id,
#             s.pricelist_id,
#             s.analytic_account_id,
#             s.team_id,
#             p.product_tmpl_id,
#             partner.country_id,
#             partner.commercial_partner_id,
#             l.discount,
#             s.id %s
#         """ % (groupby)

#         return '%s (SELECT %s FROM %s WHERE l.product_id IS NOT NULL GROUP BY %s)' % (with_, select_, from_, groupby_)

#     @api.model_cr
#     def init(self):
#         # self._table = sale_report
#         tools.drop_view_if_exists(self.env.cr, self._table)
#         self.env.cr.execute("""CREATE or REPLACE VIEW %s as (%s)""" % (self._table, self._query()))

#    @api.multi
#     def _get_report_values(self, docids, data=None):
#         docs = self.env['sale.order'].browse(docids)
#         return {
#             'doc_ids': docs.ids,
#             'doc_model': 'sale.order',
#             'docs': docs,
#             'proforma': True
#         }